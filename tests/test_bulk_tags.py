"""批量打标签（/api/admin/media/bulk 的 action=tag）回归测试。

覆盖：追加 / 替换 / 移除 / 幂等 / 上限保护 / edited_fields 标记 / 参数校验 /
不存在的索引ID被忽略。全部使用临时库与自造标签值，不接触真实数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services.tagdict import TAG_LIMIT  # noqa: E402
from app.routers import admin  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch):
    cfg = lambda: {  # noqa: E731
        "roots": [str(tmp_path)], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
        "memory_guard_bytes": 1073741824,
    }
    monkeypatch.setattr(admin.cfg_mod, "load", cfg)
    monkeypatch.setattr(admin.cfg_mod, "BASE", str(tmp_path))
    con = db.connect()
    db.init(con)
    return con


def _add_media(con, mid, title, category="视频"):
    con.execute(
        "INSERT INTO media(id, category, title, file_path, edited_fields) VALUES(?,?,?,?,'[]')",
        (mid, category, title, f"/tmp/nonexistent-{mid}.mp4"))
    con.commit()


def _tags_of(con, mid):
    return admin.media_tags(con, mid)


def test_bulk_tag_append_keeps_existing(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1, "作品甲")
    admin.set_media_tags(con, 1, ["甲标签"])
    con.commit()

    r = admin.bulk_media({"action": "tag", "mode": "append", "ids": [1],
                          "tags": ["乙标签", "丙标签"]}, None, con)
    assert r["ok"] and r["action"] == "tag" and r["mode"] == "append"
    assert r["affected"] == 1 and r["added_links"] == 2 and r["capped"] == 0
    assert sorted(_tags_of(con, 1)) == ["乙标签", "丙标签", "甲标签"] or \
        set(_tags_of(con, 1)) == {"甲标签", "乙标签", "丙标签"}
    con.close()


def test_bulk_tag_idempotent(tmp_path, monkeypatch):
    """重复追加已存在的标签 → 无变化，affected=0，不产生无谓写入。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1, "作品甲")
    admin.set_media_tags(con, 1, ["甲标签"])
    con.commit()

    r = admin.bulk_media({"action": "tag", "mode": "append", "ids": [1],
                          "tags": ["甲标签"]}, None, con)
    assert r["affected"] == 0 and r["added_links"] == 0
    assert set(_tags_of(con, 1)) == {"甲标签"}
    con.close()


def test_bulk_tag_replace(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 2, "作品乙")
    admin.set_media_tags(con, 2, ["甲标签", "乙标签"])
    con.commit()

    r = admin.bulk_media({"action": "tag", "mode": "replace", "ids": [2],
                          "tags": ["丙标签", "丁标签"]}, None, con)
    assert r["affected"] == 1 and r["added_links"] == 2 and r["removed_links"] == 2
    assert set(_tags_of(con, 2)) == {"丙标签", "丁标签"}
    con.close()


def test_bulk_tag_remove(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 3, "作品丙")
    admin.set_media_tags(con, 3, ["甲标签", "乙标签", "丙标签"])
    con.commit()

    r = admin.bulk_media({"action": "tag", "mode": "remove", "ids": [3],
                          "tags": ["乙标签"]}, None, con)
    assert r["affected"] == 1 and r["removed_links"] == 1 and r["added_links"] == 0
    assert set(_tags_of(con, 3)) == {"甲标签", "丙标签"}
    con.close()


def test_bulk_tag_respects_limit(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 4, "作品丁")
    admin.set_media_tags(con, 4, [f"已有{i}" for i in range(TAG_LIMIT)])
    con.commit()

    r = admin.bulk_media({"action": "tag", "mode": "append", "ids": [4],
                          "tags": ["溢出标签"]}, None, con)
    assert r["capped"] == 1 and r["added_links"] == 0
    assert len(_tags_of(con, 4)) == TAG_LIMIT       # 仍不超上限
    con.close()


def test_bulk_tag_marks_edited_fields(tmp_path, monkeypatch):
    """批量打标签属人工标签 → 记入 edited_fields 含 tags（防联网覆盖）。"""
    import json
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 5, "作品戊")

    admin.bulk_media({"action": "tag", "mode": "append", "ids": [5],
                      "tags": ["甲标签"]}, None, con)
    ef = json.loads(con.execute(
        "SELECT edited_fields FROM media WHERE id=5").fetchone()["edited_fields"])
    assert "tags" in ef
    con.close()


def test_bulk_tag_rejects_bad_mode_and_empty(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import pytest
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 6, "作品己")

    with pytest.raises(HTTPException):
        admin.bulk_media({"action": "tag", "mode": "bad", "ids": [6],
                          "tags": ["甲标签"]}, None, con)
    with pytest.raises(HTTPException):
        admin.bulk_media({"action": "tag", "mode": "append", "ids": [6],
                          "tags": []}, None, con)
    with pytest.raises(HTTPException):
        # 标签全为空白 → 规范化后为空 → 拒绝
        admin.bulk_media({"action": "tag", "mode": "append", "ids": [6],
                          "tags": ["   "]}, None, con)
    assert _tags_of(con, 6) == []
    con.close()


def test_bulk_tag_ignores_missing_ids(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 7, "作品庚")

    r = admin.bulk_media({"action": "tag", "mode": "append", "ids": [7, 999],
                          "tags": ["甲标签"]}, None, con)
    assert r["affected"] == 1                        # 不存在的 999 被忽略
    assert set(_tags_of(con, 7)) == {"甲标签"}
    con.close()


def test_bulk_tag_dedups_input_names(tmp_path, monkeypatch):
    """同一批 tags 内的重名会被去重，只产生一条关联。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 8, "作品辛")

    r = admin.bulk_media({"action": "tag", "mode": "append", "ids": [8],
                          "tags": ["甲标签", "甲标签", " 甲标签 "]}, None, con)
    assert r["added_links"] == 1
    assert set(_tags_of(con, 8)) == {"甲标签"}
    con.close()
