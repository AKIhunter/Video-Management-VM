"""删除作品（索引 + 磁盘真实文件）的回归测试。

全部使用临时目录 / 临时库与自造文件，不接触真实库。
断言只涉及计数 / 结构，不读取作品标题与标签文本。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.routers import admin  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [str(tmp_path)], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
        "frames_dir": str(tmp_path / "frames_cache"),
    })
    con = db.connect()
    db.init(con)
    return con


def _add_with_file(con, mid, tmp_path, with_poster_in_cache=False):
    video = tmp_path / f"v{mid}.mp4"
    video.write_bytes(b"\x00" * 10)
    poster = ""
    if with_poster_in_cache:
        cache = tmp_path / "frames_cache"
        cache.mkdir(exist_ok=True)
        poster = str(cache / f"{mid}.jpg")
        open(poster, "wb").write(b"x")
    con.execute(
        "INSERT INTO media(id, category, title, file_path, poster_path) VALUES(?,?,?,?,?)",
        (mid, "视频", f"t{mid}", str(video), poster or None))
    con.commit()
    return video, poster


def test_delete_files_removes_index_and_video(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    video, _ = _add_with_file(con, 1, tmp_path)
    r = admin.delete_media_with_files(body={"ids": [1]}, _u={}, con=con)
    assert r["deleted"] == 1 and r["files_deleted"] == 1 and r["missing"] == 0
    assert not video.exists()                                   # 视频文件已删
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    con.close()


def test_delete_files_keeps_poster_outside_managed_dirs(tmp_path, monkeypatch):
    """素材目录里的海报不是服务端缓存 → 保留并列出（不误删可能共用的图片）。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    video, poster = _add_with_file(con, 1, tmp_path)
    poster_file = tmp_path / "poster.jpg"
    poster_file.write_bytes(b"x")
    con.execute("UPDATE media SET poster_path=? WHERE id=1", (str(poster_file),))
    con.commit()
    r = admin.delete_media_with_files(body={"ids": [1]}, _u={}, con=con)
    assert r["files_deleted"] == 1                              # 视频已删
    assert poster_file.exists()                                 # 海报保留
    assert any("poster.jpg" in p for p in r["leftovers"])
    assert not video.exists()
    con.close()


def test_delete_files_removes_managed_cache_poster(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    video, poster = _add_with_file(con, 1, tmp_path, with_poster_in_cache=True)
    r = admin.delete_media_with_files(body={"ids": [1]}, _u={}, con=con)
    assert r["files_deleted"] == 1
    assert not os.path.exists(poster)                           # 服务端缓存封面一并删除
    assert r["leftovers"] == []
    con.close()


def test_delete_files_missing_video_still_removes_index(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_with_file(con, 1, tmp_path)
    con.execute("UPDATE media SET file_path=? WHERE id=1", (str(tmp_path / "gone.mp4"),))
    con.commit()
    r = admin.delete_media_with_files(body={"ids": [1]}, _u={}, con=con)
    assert r["deleted"] == 1 and r["missing"] == 1
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    con.close()


def test_delete_files_requires_ids(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        admin.delete_media_with_files(body={"ids": []}, _u={}, con=con)
    assert e.value.status_code == 400
    con.close()


def test_delete_files_clears_associations(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    video, _ = _add_with_file(con, 1, tmp_path)
    con.execute("INSERT INTO tags(id, name) VALUES(9, 'x')")
    con.execute("INSERT INTO media_tags(media_id, tag_id) VALUES(1, 9)")
    con.execute("INSERT INTO watch_state(media_id, user_id, favorite) VALUES(1, 1, 1)")
    con.commit()
    admin.delete_media_with_files(body={"ids": [1]}, _u={}, con=con)
    assert con.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1   # 标签字典不受影响
    assert not video.exists()
    con.close()
