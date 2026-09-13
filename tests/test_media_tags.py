"""标签增删接口（media.py）的单元测试。"""
import json

import pytest
from fastapi import HTTPException

from app import db
from app.routers import media


def _mk_db(tmp_path, monkeypatch):
    monkeypatch.setattr(media.cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    db.init(con)
    con.execute(
        "INSERT INTO media(id, category, title, file_path, edited_fields) "
        "VALUES(1, '视频', '测试作品', '/x/1.mp4', '[]')")
    con.commit()
    return con


def _add(con, mid, name):
    # _u 是鉴权占位（路由层由 Depends 注入），单元测试直接给个 admin 身份
    return media.add_tag(mid, media.TagIn(name=name), _u={"id": 1, "role": "admin"}, con=con)


def _rm(con, mid, name):
    return media.remove_tag(mid, media.TagIn(name=name), _u={"id": 1, "role": "admin"}, con=con)


def test_normalize_tag():
    assert media._normalize_tag("  校园  ") == "校园"
    assert media._normalize_tag("") == ""
    assert len(media._normalize_tag("这是一个非常长的标签名字超过十个字符")) <= media.MAX_TAG_LEN


def test_add_and_remove(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    try:
        assert set(_add(con, 1, "悬疑")["tags"]) == {"悬疑"}
        assert set(_add(con, 1, "校园")["tags"]) == {"悬疑", "校园"}
        # 去重：重复添加不产生重复项
        assert set(_add(con, 1, "悬疑")["tags"]) == {"悬疑", "校园"}
        # 上限：已有 2 个，依次加到 TAG_LIMIT 个成功，第 TAG_LIMIT+1 个 400
        fill = ["喜剧", "奇幻", "科幻", "音乐", "竞技", "日常", "恋爱", "运动"]  # 2 + 8 = 10
        for name in fill:
            _add(con, 1, name)
        # 此时共 10 个（= TAG_LIMIT），再加第 11 个应被拒
        with pytest.raises(HTTPException):
            _add(con, 1, "冒险")
        # 删除
        assert set(_rm(con, 1, "悬疑")["tags"]) == ({"悬疑", "校园"} | set(fill)) - {"悬疑"}
    finally:
        con.close()


def test_add_marks_edited(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    try:
        _add(con, 1, "悬疑")
        row = con.execute("SELECT edited_fields FROM media WHERE id=1").fetchone()
        assert "tags" in json.loads(row["edited_fields"])
    finally:
        con.close()


def test_add_404(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    try:
        with pytest.raises(HTTPException):
            _add(con, 999, "悬疑")
    finally:
        con.close()
