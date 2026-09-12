"""分类字典（扫描可选分类 / 作品管理 / 首页筛选共用）+ 作品管理（移动 / 删索引）+ ID 导出。

全部使用临时库与自造数据，不接触真实数据。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.routers import admin, media  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch):
    cfg = lambda: {  # noqa: E731
        "roots": [str(tmp_path)], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
        "memory_guard_bytes": 1073741824,
    }
    monkeypatch.setattr(admin.cfg_mod, "load", cfg)
    monkeypatch.setattr(media.cfg_mod, "load", cfg)
    monkeypatch.setattr(admin.cfg_mod, "BASE", str(tmp_path))
    con = db.connect()
    db.init(con)
    return con


def _add_media(con, mid, title, category="视频", path=None):
    con.execute(
        "INSERT INTO media(id, category, title, file_path, edited_fields) VALUES(?,?,?,?,'[]')",
        (mid, category, title, path or f"/tmp/nonexistent-{mid}.mp4"))
    con.commit()


# ---------------- 分类字典 ----------------
def test_categories_crud_and_stats_coupling(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import pytest
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1, "作品甲")
    _add_media(con, 2, "作品乙", category="3D")

    items = admin.list_categories(None, con)["items"]
    names = {i["name"]: i["c"] for i in items}
    assert names.get("视频") == 1 and names.get("3D") == 1   # 自动同步 media 中出现的分类

    admin.create_category({"name": "新分类"}, None, con)
    names = {i["name"]: i["c"] for i in admin.list_categories(None, con)["items"]}
    assert names.get("新分类") == 0                          # 空分类也进字典

    # 首页筛选的分类下拉与字典同源（空分类也出现）
    stat_names = {c["category"]: c["c"] for c in media.stats(con=con)["categories"]}
    assert "新分类" in stat_names and stat_names["新分类"] == 0

    # 重命名：同步更新该分类下所有作品
    cid = next(i["id"] for i in admin.list_categories(None, con)["items"] if i["name"] == "3D")
    r = admin.rename_category(cid, {"name": "3D重命名"}, None, con)
    assert r["moved"] == 1
    assert con.execute("SELECT COUNT(*) FROM media WHERE category='3D重命名'").fetchone()[0] == 1

    # 删空分类可以；删非空分类拒绝
    cid_empty = next(i["id"] for i in admin.list_categories(None, con)["items"] if i["name"] == "新分类")
    assert admin.delete_category(cid_empty, None, con)["ok"] is True
    cid_nonempty = next(i["id"] for i in admin.list_categories(None, con)["items"] if i["name"] == "视频")
    with pytest.raises(HTTPException):
        admin.delete_category(cid_nonempty, None, con)
    con.close()


# ---------------- 作品管理列表 + 模糊查询 ----------------
def test_media_list_fuzzy_query(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 101, "某作品")
    _add_media(con, 202, "另一个", category="3D")
    con.execute("INSERT INTO tags(name) VALUES('甲标签')")
    tid = con.execute("SELECT id FROM tags WHERE name='甲标签'").fetchone()["id"]
    con.execute("INSERT INTO media_tags(media_id, tag_id) VALUES(101, ?)", (tid,))
    con.commit()

    assert admin.list_media_admin(category="", q="", page=1, size=50, _u=None, con=con)["total"] == 2
    assert admin.list_media_admin(category="3D", q="", page=1, size=50, _u=None, con=con)["total"] == 1
    assert admin.list_media_admin(category="", q="101", page=1, size=50, _u=None, con=con)["total"] == 1  # 按索引ID
    assert admin.list_media_admin(category="", q="另一个", page=1, size=50, _u=None, con=con)["total"] == 1  # 按名称
    assert admin.list_media_admin(category="", q="甲标签", page=1, size=50, _u=None, con=con)["total"] == 1  # 按标签
    con.close()


# ---------------- 批量移动 / 批量删索引 ----------------
def test_bulk_move_and_delete_keeps_files(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    vf1 = tmp_path / "keep1.mp4"
    vf2 = tmp_path / "keep2.mp4"
    vf1.write_bytes(b"x")
    vf2.write_bytes(b"x")
    _add_media(con, 1, "待移动", path=str(vf1))
    _add_media(con, 2, "待删除", path=str(vf2))

    r = admin.bulk_media({"action": "move", "ids": [1, 2], "category": "目标分类"}, None, con)
    assert r["moved"] == 2 and r["category"] == "目标分类"
    assert con.execute("SELECT COUNT(*) FROM media WHERE category='目标分类'").fetchone()[0] == 2

    # 给待删作品挂上关联数据，验证一并清理
    con.execute("INSERT INTO tags(name) VALUES('标签X')")
    tid = con.execute("SELECT id FROM tags WHERE name='标签X'").fetchone()["id"]
    con.execute("INSERT INTO media_tags(media_id, tag_id) VALUES(2, ?)", (tid,))
    con.execute("INSERT INTO watch_state(media_id, user_id) VALUES(2, 1)")
    con.commit()

    d = admin.bulk_media({"action": "delete", "ids": [2]}, None, con)
    assert d["deleted"] == 1
    assert con.execute("SELECT COUNT(*) FROM media WHERE id=2").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM media_tags WHERE media_id=2").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM watch_state WHERE media_id=2").fetchone()[0] == 0
    assert vf2.exists(), "删索引绝不能删除磁盘文件"
    con.close()


# ---------------- 下载索引ID 跟随查询条件 ----------------
def _ids_in(resp):
    return [int(x) for x in resp.body.decode("utf-8").split() if x.strip()]


def test_ids_export_follows_query(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 11, "作品甲")
    _add_media(con, 12, "作品乙", category="3D")
    _add_media(con, 13, "作品丙")

    assert _ids_in(admin.download_media_ids(con=con)) == [11, 12, 13]        # 不筛选=全部
    assert _ids_in(admin.download_media_ids(q="作品甲", con=con)) == [11]
    assert _ids_in(admin.download_media_ids(category="3D", con=con)) == [12]
    assert _ids_in(admin.download_media_ids(q="不存在", con=con)) == []
    con.close()


# ---------------- 扫描：指定分类（override） ----------------
def test_scan_with_category_override(tmp_path, monkeypatch):
    from app.services import scanner
    con = _mk_cfg(tmp_path, monkeypatch)
    con.close()
    p = tmp_path / "2022年视频" / "[无名]作品A.chs.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 10)

    # 目录名推出的分类是「视频」，但显式指定分类后一律记入该分类
    r = scanner.do_scan(scope="path", path=str(tmp_path), dry_run=False,
                        category_override="自定分类")
    assert r["added"] >= 1
    con = db.connect()
    rows = con.execute("SELECT category FROM media").fetchall()
    assert [x["category"] for x in rows] == ["自定分类"]
    con.close()

    # 重扫换分类：不新增，改分类（不报 UNIQUE 冲突）
    r2 = scanner.do_scan(scope="path", path=str(tmp_path), dry_run=False,
                         category_override="另一个分类")
    assert r2["added"] == 0
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    assert con.execute("SELECT category FROM media").fetchone()["category"] == "另一个分类"
    con.close()


def test_scan_registers_category(tmp_path, monkeypatch):
    """通过接口启动扫描时（带 category），该分类会登记进分类字典。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1, "作品甲")
    admin.trigger_scan({"scope": "path", "path": str(tmp_path), "category": "扫描新分类"}, None, con)
    names = [i["name"] for i in admin.list_categories(None, con)["items"]]
    assert "扫描新分类" in names
    # 清理：取消可能启动的任务（互斥槽）
    from app.services import jobs
    j = jobs.current()
    if j is not None and j.kind == "scan":
        j.cancel()
    con.close()
