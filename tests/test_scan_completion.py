"""扫描（路径/全盘/可取消）与全量联网补全 的工作流测试。"""
import os

import pytest

from app.services import jobs
from app.services.jobs import Job


@pytest.fixture
def fake_cfg(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "load", lambda: {
        "roots": [str(tmp_path)],
        "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"),
        "memory_guard_bytes": 1073741824,
    })
    return tmp_path


def _make_video(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 10)
    return p


class TestScanScopes:
    def test_path_scan_indexes_video(self, fake_cfg):
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品A.chs.mp4")

        from app.services import scanner
        r = scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        assert r["added"] >= 1
        assert r["scope"] == "path"

        con = db.connect()
        row = con.execute("SELECT * FROM media LIMIT 1").fetchone()
        assert row and row["title"]
        assert row["category"] == "视频"

    def test_path_scan_skips_global_rating(self, fake_cfg):
        _make_video(fake_cfg, "2022年视频/[无名]作品B.chs.mp4")
        from app.services import scanner
        r = scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        assert r["scope"] == "path"
        assert r["rating_hits"] == 0

    def test_cancel_stops_before_processing(self, fake_cfg):
        _make_video(fake_cfg, "2022年视频/[无名]作品C.chs.mp4")
        import threading
        stop = threading.Event()
        stop.set()  # 预先取消

        from app.services import scanner
        r = scanner.do_scan(scope="path", path=str(fake_cfg),
                            dry_run=False, stop=stop)
        assert r["canceled"] is True

    def test_path_scan_does_not_delete_out_of_scope(self, fake_cfg):
        """定点扫描绝不能删除扫描范围之外的媒体（回归：曾误删全库）。"""
        import app.db as db
        # 先全量扫描建立两条记录
        _make_video(fake_cfg, "2022年视频/A目录/[X]作品X.chs.mp4")
        _make_video(fake_cfg, "2022年视频/B目录/[Y]作品Y.chs.mp4")
        from app.services import scanner
        scanner.do_scan(scope="full", dry_run=False)

        n_before = db.connect().execute("SELECT COUNT(*) c FROM media").fetchone()["c"]
        assert n_before == 2

        # 只定点扫描 A 目录（B 目录不在范围内）
        a_dir = fake_cfg / "2022年视频" / "A目录"
        r = scanner.do_scan(scope="path", path=str(a_dir), dry_run=False)
        n_after = db.connect().execute("SELECT COUNT(*) c FROM media").fetchone()["c"]
        assert n_after == 2, "定点扫描不应删除范围外的媒体"


class FakeResolver:
    name = "fake"

    def resolve(self, media_row):
        return {
            "synopsis": "一段可供回填的简介，长度足够填充。",
            "tags": ["剧情", "纯爱"],
            "source": "fake", "confidence": 0.9,
        }


class TestCompletion:
    def test_injects_synopsis_and_tags(self, fake_cfg):
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品D.chs.mp4")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)

        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]

        job = Job("completion", lambda j: None)
        result = completion.run_completion(job, mid=mid, resolver=FakeResolver())
        assert result["filled"] >= 1

        row = con.execute("SELECT synopsis FROM media WHERE id=?", (mid,)).fetchone()
        assert row["synopsis"] == "一段可供回填的简介，长度足够填充。"
        con.close()

    def test_write_boundary_truncates_synopsis(self, fake_cfg):
        """写入边界强制简介≤500字：即便解析器返回超长文本也不越界存储。"""
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品W.chs.mp4")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)

        class LongResolver:
            name = "long"
            def resolve(self, media_row):
                return {"synopsis": "超长" * 400, "tags": []}

        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]
        job = Job("completion", lambda j: None)
        completion.run_completion(job, mid=mid, resolver=LongResolver())
        syn = con.execute("SELECT synopsis FROM media WHERE id=?", (mid,)).fetchone()["synopsis"]
        assert len(syn.rstrip("…")) <= 500  # 正文内容 ≤500 字（可带截断省略号）
        con.close()

    def test_edited_fields_not_overwritten(self, fake_cfg):
        import json
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品E.chs.mp4")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)

        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]
        # 模拟人工已编辑简介
        con.execute("UPDATE media SET synopsis='人工撰写简介', edited_fields=? WHERE id=?",
                    (json.dumps(["synopsis"]), mid))
        con.commit()

        job = Job("completion", lambda j: None)
        result = completion.run_completion(job, mid=mid, resolver=FakeResolver())
        row = con.execute("SELECT synopsis FROM media WHERE id=?", (mid,)).fetchone()
        assert row["synopsis"] == "人工撰写简介"  # 未被覆盖

    def test_cancel_reports_canceled(self, fake_cfg):
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品F.chs.mp4")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]

        job = Job("completion", lambda j: None)
        job.cancel()  # 预先取消
        result = completion.run_completion(job, mid=mid, resolver=FakeResolver())
        assert result["canceled"] is True
        assert result["filled"] == 0

    def test_date_filled_from_path(self, fake_cfg):
        """缺年月的作品：从文件夹路径派生补全 publish_date（模拟旧记录年份丢失）。"""
        import app.db as db
        _make_video(fake_cfg, "2022年视频/2022年05月合集/[无名]作品G.chs.mp4")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]
        # 模拟旧记录缺失年月（扫描本会回填，这里手动清空以触发补全路径）
        con.execute("UPDATE media SET publish_date=NULL, year=NULL WHERE id=?", (mid,))
        con.commit()

        class NoDateResolver:
            name = "nodate"
            def resolve(self, media_row):
                return {"synopsis": None, "tags": []}

        job = Job("completion", lambda j: None)
        result = completion.run_completion(job, mid=mid, resolver=NoDateResolver())
        row = con.execute("SELECT publish_date, year FROM media WHERE id=?", (mid,)).fetchone()
        assert row["publish_date"] == "2022-05-01"
        assert row["year"] == 2022
        assert result["date_filled"] >= 1 and result["year_filled"] >= 1
        con.close()

    def test_date_filled_from_web_candidate(self, fake_cfg):
        """路径无月份信息时，用联网候选 publish_date 兜底补全。"""
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品H.chs.mp4")  # 仅有年份目录、无月份
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]
        # 清空 publish_date 以触发补全（扫描已填 year=2022，但无月份）
        con.execute("UPDATE media SET publish_date=NULL WHERE id=?", (mid,))
        con.commit()

        class WebDateResolver:
            name = "webdate"
            def resolve(self, media_row):
                return {"synopsis": None, "tags": [], "publish_date": "2023-03-15"}

        job = Job("completion", lambda j: None)
        result = completion.run_completion(job, mid=mid, resolver=WebDateResolver())
        row = con.execute("SELECT publish_date FROM media WHERE id=?", (mid,)).fetchone()
        assert row["publish_date"] == "2023-03-01"  # 规范化为 YYYY-MM-01
        assert result["date_filled"] >= 1
        con.close()

    def test_local_tag_fallback(self, fake_cfg):
        """联网无题材 tag 且作品无标签时，以本机简评打标 fallback。"""
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]打标测试.chs.mp4")
        # 简评文件：roots 下任意位置，文件名含「简评」且 .txt
        (fake_cfg / "简评.txt").write_text("《打标测试》本作战斗场面激烈，校园与日常背景", encoding="utf-8")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]

        class NoTagResolver:
            name = "notag"
            def resolve(self, media_row):
                return {"synopsis": "有简介不影响打标", "tags": ["剧情", "纯爱"]}  # 非题材词，被过滤为空

        job = Job("completion", lambda j: None)
        result = completion.run_completion(job, mid=mid, resolver=NoTagResolver())
        tags = [r["name"] for r in con.execute(
            "SELECT t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=?", (mid,)).fetchall()]
        assert "动作" in tags and "校园" in tags
        assert result["tags_local"] >= 1
        con.close()

    def test_ids_scope_strict(self, fake_cfg):
        """ids 范围：只补指定作品，不存在的 ID 记入 missing。"""
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品I.chs.mp4")
        _make_video(fake_cfg, "2022年视频/[无名]作品J.chs.mp4")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        con = db.connect()
        ids = [r["id"] for r in con.execute("SELECT id FROM media ORDER BY id").fetchall()]
        con.close()

        job = Job("completion", lambda j: None)
        result = completion.run_completion(job, ids=[ids[0], 999999], resolver=FakeResolver())
        assert result["scope"] == "ids"
        assert result["total"] == 1
        assert 999999 in result["missing"]

    def test_edited_date_not_overwritten(self, fake_cfg):
        """人工已编辑 year/publish_date 时，联网补全不覆盖。"""
        import json
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[无名]作品K.chs.mp4")
        from app.services import completion, scanner
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=False)
        con = db.connect()
        mid = con.execute("SELECT id FROM media LIMIT 1").fetchone()["id"]
        con.execute(
            "UPDATE media SET year=2099, publish_date='2099-09-01', edited_fields=? WHERE id=?",
            (json.dumps(["year", "publish_date"]), mid))
        con.commit()

        class WebDateResolver:
            name = "webdate"
            def resolve(self, media_row):
                return {"synopsis": None, "tags": [], "publish_date": "2023-03-15"}

        job = Job("completion", lambda j: None)
        completion.run_completion(job, mid=mid, resolver=WebDateResolver())
        row = con.execute("SELECT publish_date, year FROM media WHERE id=?", (mid,)).fetchone()
        assert row["publish_date"] == "2099-09-01"
        assert row["year"] == 2099
        con.close()


def test_job_status_exposes_cancel(tmp_path):
    j = Job("scan", lambda jj: None)
    j.cancel()
    st = j.status()
    assert st["canceled"] is True