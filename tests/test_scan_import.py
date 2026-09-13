"""两段式扫描（扫描 → 暂存清单 → 人工导入）的导入防呆与二次校验。

全部使用临时目录 / 临时库与自造文件，不接触真实库。
断言只涉及计数 / 类别字符串 / 结构，不读取标签文本。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routers import admin  # noqa: E402
from app.services import scan_stage, scanner  # noqa: E402


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


class TestStagedScan:
    def test_scan_fills_stage_without_writing_db(self, fake_cfg):
        import app.db as db
        _make_video(fake_cfg, "2022年视频/[A]作品A.chs.mp4")
        stage = []
        r = scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=True, stage=stage)
        assert r["dry_run"] is True and r["added"] == 1
        assert len(stage) == 1 and stage[0]["action"] == "add"
        assert stage[0]["category"] == "视频"
        con = db.connect()
        assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0   # 未写库

    def test_import_writes_with_forced_category(self, fake_cfg):
        import app.db as db
        p = _make_video(fake_cfg, "2022年视频/[A]作品B.chs.mp4")
        stage = []
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=True, stage=stage)
        r = scanner.import_staged(stage, "真人")          # 分类由人工显式指定
        assert r["added"] == 1 and r["skipped"] == 0
        con = db.connect()
        row = con.execute("SELECT category, file_path FROM media").fetchone()
        assert row["category"] == "真人" and row["file_path"] == str(p)

    def test_import_skips_missing_and_changed_files(self, fake_cfg):
        p1 = _make_video(fake_cfg, "2022年视频/[A]作品C.chs.mp4")
        p2 = _make_video(fake_cfg, "2022年视频/[A]作品D.chs.mp4")
        stage = []
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=True, stage=stage)
        assert len(stage) == 2
        p1.unlink()                                       # 文件已消失 → 跳过
        p2.write_bytes(b"\x00" * 99)                      # 指纹已变化 → 跳过
        r = scanner.import_staged(stage, "视频")
        assert r["skipped"] == 2 and r["added"] == 0

    def test_existing_entry_is_updated_not_duplicated(self, fake_cfg):
        import app.db as db
        p = _make_video(fake_cfg, "2022年视频/[A]作品E.chs.mp4")
        stage = []
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=True, stage=stage)
        scanner.import_staged(stage, "视频")
        stage2 = []
        scanner.do_scan(scope="path", path=str(fake_cfg), dry_run=True, stage=stage2)
        assert stage2 == []                                # 指纹未变 → 不进清单
        con = db.connect()
        assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1

    def test_stage_store_roundtrip(self):
        scan_stage.set_stage([{"action": "add", "title": "x", "scan_hash": "1:2",
                               "file_path": "/tmp/a.mp4"}], {"scope": "path"})
        assert scan_stage.summary()["total"] == 1
        assert scan_stage.items()[0]["title"] == "x"
        v = scan_stage.view()[0]
        assert v["title"] == "x" and v["file_path"] == "/tmp/a.mp4"
        assert "scan_hash" not in v                        # 内部字段不外露
        assert scan_stage.clear() == 1
        assert scan_stage.summary()["total"] == 0


class TestImportGuard:
    """导入防呆：必须人工选择具体分类，拒绝「自动（按目录名）」与空清单。"""

    def test_requires_category(self):
        with pytest.raises(HTTPException) as e:
            admin.scan_import(body={"category": ""}, _u={"id": 1, "role": "admin"})
        assert e.value.status_code == 400

    def test_rejects_auto_category(self):
        for cat in ("自动", "自动（按目录名）"):
            with pytest.raises(HTTPException) as e:
                admin.scan_import(body={"category": cat}, _u={"id": 1, "role": "admin"})
            assert e.value.status_code == 400

    def test_rejects_empty_stage(self):
        scan_stage.clear()
        with pytest.raises(HTTPException) as e:
            admin.scan_import(body={"category": "视频"}, _u={"id": 1, "role": "admin"})
        assert e.value.status_code == 400
