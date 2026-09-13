"""白屏管理 · 重复作品检测（标题高度相似分组）。

全部使用临时库与自造数据，不接触真实库。
断言只涉及 id 集合 / 计数 / 结构。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.routers import admin  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    db.init(con)
    return con


def _add(con, mid, title, category="视频"):
    con.execute(
        "INSERT INTO media(id, category, title, file_path) VALUES(?,?,?,?)",
        (mid, category, title, f"/tmp/v-{mid}.mp4"))
    con.commit()


def _group_sets(result):
    return sorted([sorted(it["id"] for it in g["items"]) for g in result["groups"]])


def test_exact_duplicates_grouped_and_isolated(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Dup Sample Alpha")
    _add(con, 2, "Dup Sample Alpha")           # 归一化后完全相同
    _add(con, 3, "Another Movie Beta")
    _add(con, 4, "Another Movie Beta")         # 另一组（与上一组无共同片段）
    _add(con, 5, "Totally Unrelated Thing")    # 孤立，不应成组

    res = admin._dup_groups(con, category="视频")
    assert _group_sets(res) == [[1, 2], [3, 4]]
    assert res["scanned"] == 5


def test_near_duplicate_grouped(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Sample Title 02")
    _add(con, 2, "Sample Title 03")            # 仅差 1 个字符 → 高度相似
    assert _group_sets(admin._dup_groups(con, category="视频")) == [[1, 2]]


def test_unrelated_titles_not_grouped(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Completely Different One")
    _add(con, 2, "Another Unrelated Item")
    assert admin._dup_groups(con, category="视频")["groups"] == []


def test_category_isolation(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Same Name Here", category="视频")
    _add(con, 2, "Same Name Here", category="其它")
    _add(con, 3, "Same Name Here", category="其它")
    assert admin._dup_groups(con, category="视频")["groups"] == []   # 该分类只有 1 条
    assert _group_sets(admin._dup_groups(con, category="其它")) == [[2, 3]]


def test_threshold_is_respected(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Alpha Beta Gamma")
    _add(con, 2, "Alpha Beta Delta")         # 相似度中等
    loose = admin._dup_groups(con, category="视频", threshold=0.6)
    strict = admin._dup_groups(con, category="视频", threshold=0.99)
    assert _group_sets(loose) == [[1, 2]]
    assert strict["groups"] == []


def test_empty_title_not_grouped(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "")
    _add(con, 2, "")
    assert admin._dup_groups(con, category="视频")["groups"] == []


def test_items_expose_only_safe_fields(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Field Check Item")
    _add(con, 2, "Field Check Item")
    item = admin._dup_groups(con, category="视频")["groups"][0]["items"][0]
    assert set(item) == {"id", "category", "title", "title_jp", "year", "has_cover"}
    assert "file_path" not in item


# ---------------- 「本组忽略」：忽略后不再检出，可撤销，删代表项也依然生效 ----------------
def test_ignore_group_hides_and_undo_restores(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Dup Sample Alpha")
    _add(con, 2, "Dup Sample Alpha")
    key = admin._dup_groups(con, category="视频")["groups"][0]["key"]
    assert len(admin._dup_groups(con, category="视频")["groups"]) == 1

    r = admin.ignore_duplicate_group(body={"key": key}, _u={}, con=con)
    assert r["ok"] is True
    res = admin._dup_groups(con, category="视频")
    assert res["groups"] == [] and res["ignored_groups"] == 1

    admin.ignore_duplicate_group(body={"key": key, "undo": True}, _u={}, con=con)
    assert len(admin._dup_groups(con, category="视频")["groups"]) == 1


def test_ignore_survives_rep_deletion(tmp_path, monkeypatch):
    """忽略按组内标题集合记录：删掉代表项（最小 id）后，剩余成员仍被识别为已忽略。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Sample Title 02")     # 组代表
    _add(con, 2, "Sample Title 03")
    _add(con, 3, "Sample Title 03")
    key = admin._dup_groups(con, category="视频")["groups"][0]["key"]
    admin.ignore_duplicate_group(body={"key": key}, _u={}, con=con)

    con.execute("DELETE FROM media WHERE id=1")
    con.commit()
    res = admin._dup_groups(con, category="视频")
    assert res["groups"] == []          # 剩 2 条仍会成组，但已被忽略
    assert res["ignored_groups"] == 1


def test_ignore_clear_all(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "Dup Sample Alpha")
    _add(con, 2, "Dup Sample Alpha")
    key = admin._dup_groups(con, category="视频")["groups"][0]["key"]
    admin.ignore_duplicate_group(body={"key": key}, _u={}, con=con)
    assert admin._dup_groups(con, category="视频")["groups"] == []
    admin.ignore_duplicate_group(body={"clear": True}, _u={}, con=con)
    assert len(admin._dup_groups(con, category="视频")["groups"]) == 1
