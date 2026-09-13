"""封面扩展能力：指定目录补全 / 视频帧封面标记 / 审定列表按步骤B范围筛选。

全部使用临时库与自造数据，不接触真实数据。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import covers  # noqa: E402
from app.services import online_covers as oc  # noqa: E402
from app.routers import admin, media  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    monkeypatch.setattr(cfg_mod, "BASE", str(tmp_path))
    monkeypatch.setattr(oc.cfg_mod, "BASE", str(tmp_path))
    monkeypatch.setattr(covers.cfg_mod, "BASE", str(tmp_path))
    con = db.connect()
    db.init(con)
    return con


def _add(con, mid, title, poster=None, edited=None, meta=None):
    con.execute(
        "INSERT INTO media(id, category, title, file_path, poster_path, edited_fields, meta) "
        "VALUES(?,?,?,?,?,?,?)",
        (mid, "视频", title, f"/tmp/none-{mid}.mp4", poster,
         json.dumps(edited or []), json.dumps(meta or {})))
    con.commit()


# ---------------- 指定目录补全 ----------------
def test_cover_dir_backfill_matches_and_respects_guards(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    # 缺封面的作品（可被匹配）
    _add(con, 1, "标题甲")
    # 已有封面的作品（跳过）
    have = tmp_path / "have.jpg"
    have.write_bytes(b"x")
    _add(con, 2, "标题乙", poster=str(have))
    # 人工设过封面的作品（跳过）
    _add(con, 3, "标题丙", edited=["poster_path"])
    # 目录里没有对应图片的作品（不匹配）
    _add(con, 4, "根本没有的标题")

    cdir = tmp_path / "封面素材"
    cdir.mkdir()
    (cdir / "标题甲.jpg").write_bytes(b"img")
    (cdir / "标题乙.png").write_bytes(b"img")
    (cdir / "标题丙.webp").write_bytes(b"img")
    (cdir / "无关图片.jpg").write_bytes(b"img")

    r = covers.run_cover_dir_backfill({"category_filter": ["视频"]}, str(cdir))
    assert r["images"] == 4
    assert r["matched"] == 1                  # 只有 id=1 被写入
    assert r["skipped_has_cover"] == 1        # id=2
    assert r["skipped_edited"] == 1           # id=3
    assert r["no_match"] == 1                 # id=4
    got = con.execute("SELECT poster_path FROM media WHERE id=1").fetchone()["poster_path"]
    assert got.endswith("标题甲.jpg") and os.path.exists(got)
    # 只写路径引用：源图片仍在原处、未被移动
    assert (cdir / "标题甲.jpg").exists()
    con.close()


def test_cover_dir_backfill_ids_scope(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "标题甲")
    _add(con, 2, "标题乙")
    cdir = tmp_path / "c"
    cdir.mkdir()
    (cdir / "标题甲.jpg").write_bytes(b"i")
    (cdir / "标题乙.jpg").write_bytes(b"i")
    r = covers.run_cover_dir_backfill({"category_filter": ["视频"]}, str(cdir), ids=[2])
    assert r["matched"] == 1
    assert con.execute("SELECT poster_path FROM media WHERE id=1").fetchone()["poster_path"] is None
    con.close()


def test_cover_dir_endpoint_rejects_bad_path(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import pytest
    _mk_cfg(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        admin.trigger_cover_dir({"path": str(tmp_path / "不存在")}, None)
    with pytest.raises(HTTPException):
        admin.trigger_cover_dir({"path": ""}, None)


# ---------------- 旧版「视频帧封面标记」遗留数据兼容（界面入口已移除，由服务端抽帧替代） ----------------
def test_video_frame_mode_legacy_meta_still_serialized(tmp_path, monkeypatch):
    """历史上标记过 cover_mode=video_frame 的作品：序列化仍暴露该值（前端可渲染），
    待服务端抽帧写回 poster_path 后由 frames 清掉该标记。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "标题甲", meta={"cover_mode": "video_frame"})
    row = con.execute("SELECT m.*, NULL AS status, NULL AS personal_rating, NULL AS favorite, "
                      "NULL AS note FROM media m WHERE m.id=1").fetchone()
    assert media.serialize(row)["cover_mode"] == "video_frame"
    con.close()


# ---------------- 审定列表按步骤B范围筛选 ----------------
def test_cover_reviews_scope_filter(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "标题甲")
    _add(con, 2, "标题乙")
    cand = {"candidates": ["https://x/1.jpg"], "confidence": 0.3,
            "post_url": "https://p/1", "title_matched": "t", "error": None}
    oc.save_review(con, 1, cand)
    oc.save_review(con, 2, cand)
    con.commit()

    # 未记录范围：不筛选
    r0 = admin.list_cover_reviews(source="last_cover_online", _u=None, con=con)
    assert len(r0["items"]) == 2 and r0["filtered"] is False and r0["scope_ids"] == 0

    # 记录范围为 [2] → 只保留 media_id=2 的那条
    db.set_setting(con, "cover_online_scope", json.dumps([2]))
    r1 = admin.list_cover_reviews(source="last_cover_online", _u=None, con=con)
    assert r1["filtered"] is True and r1["scope_ids"] == 1
    assert [i["media_id"] for i in r1["items"]] == [2]
    assert r1["dropped"] == 1

    # 不传 source：全部返回
    r2 = admin.list_cover_reviews(_u=None, con=con)
    assert len(r2["items"]) == 2
    con.close()


def test_online_backfill_skips_video_frame(tmp_path, monkeypatch):
    """已标记视频帧封面的作品不再进入联网搜封面目标。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    _add(con, 11, "甲", meta={"cover_mode": "video_frame"})
    _add(con, 12, "乙")
    con.execute("UPDATE media SET file_path=? WHERE id=11", (str(v1),))
    con.execute("UPDATE media SET file_path=? WHERE id=12", (str(v2),))
    con.commit()
    con.close()

    class J:
        stop = type("E", (), {"is_set": lambda self: False})()
        meta = {}

        def begin(self, *a, **k): pass

        def tick(self, *a, **k): pass

    monkeypatch.setattr(oc, "search_cover", lambda row: {"candidates": [], "error": "无源"})
    res = oc.run_online_cover_backfill(J())
    assert res["total"] == 1        # 只剩没有视频帧标记的那部
