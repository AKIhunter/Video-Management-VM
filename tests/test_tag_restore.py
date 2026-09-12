"""按导出 JSON 还原 media_tags 关联：只重建 id↔id，不写入/不输出标签值。

全部使用临时库与自造标签值，不接触真实数据。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import tag_restore  # noqa: E402
from app.services.tagdict import TAG_LIMIT  # noqa: E402


def _mk_db(tmp_path, monkeypatch):
    from app import config as cfg
    monkeypatch.setattr(cfg, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    db.init(con)
    for i in (1, 2, 3):
        con.execute(
            "INSERT INTO media(id, category, title, file_path, edited_fields) "
            "VALUES(?, '视频', ?, ?, '[]')", (i, f"作品{i}", f"/x/{i}.mp4"))
    for name in ("甲", "乙"):
        con.execute("INSERT INTO tags(name) VALUES(?)", (name,))
    con.commit()
    return con


def _payload():
    return {"version": 1, "tags": [
        {"name": "甲", "media_ids": [1, 2, 99]},   # 99 不存在
        {"name": "乙", "media_ids": [2]},
        {"name": "库中没有的标签", "media_ids": [1]},  # 未匹配 → 不新建
    ]}


def test_join_table_has_only_ids(tmp_path, monkeypatch):
    """关联表必须只存 id：列只有 media_id / tag_id。"""
    con = _mk_db(tmp_path, monkeypatch)
    cols = [r["name"] for r in con.execute("PRAGMA table_info(media_tags)").fetchall()]
    assert cols == ["media_id", "tag_id"]
    con.close()


def test_dry_run_reports_without_writing(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    st = tag_restore.restore(con, _payload(), dry_run=True)
    assert st["file_tags"] == 3
    assert st["matched_tags"] == 2 and st["unmatched_tags"] == 1
    assert st["links_added"] == 3          # 甲→1,2 + 乙→2
    assert st["missing_media"] == 1        # 99
    assert st["links_before"] == 0 and st["links_after"] == 0
    assert con.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0] == 0
    assert st["total_tags"] == 2           # 未匹配的标签不会被新建
    con.close()


def test_apply_writes_id_only_and_is_idempotent(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    st = tag_restore.restore(con, _payload(), dry_run=False)
    assert st["links_added"] == 3 and st["links_after"] == 3
    rows = con.execute("SELECT media_id, tag_id FROM media_tags ORDER BY media_id, tag_id").fetchall()
    assert [(r["media_id"], r["tag_id"]) for r in rows] == [(1, 1), (2, 1), (2, 2)]
    # 未新建标签
    assert con.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 2
    # 再跑一次：全部已存在，新增 0
    st2 = tag_restore.restore(con, _payload(), dry_run=False)
    assert st2["links_added"] == 0 and st2["skipped_existing"] == 3
    con.close()


def test_restore_respects_tag_limit(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    for i in range(TAG_LIMIT):
        con.execute("INSERT INTO tags(name) VALUES(?)", (f"饱和{i}",))
    con.commit()
    names = [r["name"] for r in con.execute(
        "SELECT name FROM tags WHERE name LIKE '饱和%' ORDER BY id").fetchall()]
    # 让作品 1 先占满 TAG_LIMIT 个
    con.execute("INSERT INTO tags(name) VALUES('溢出')")
    tid = con.execute("SELECT id FROM tags WHERE name='溢出'").fetchone()["id"]
    for n in names:
        t = con.execute("SELECT id FROM tags WHERE name=?", (n,)).fetchone()["id"]
        con.execute("INSERT INTO media_tags(media_id, tag_id) VALUES(1, ?)", (t,))
    con.commit()
    st = tag_restore.restore(con, {"tags": [{"name": "溢出", "media_ids": [1]}]}, dry_run=False)
    assert st["links_added"] == 0 and st["capped"] == 1
    assert con.execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=?", (tid,)).fetchone()[0] == 0
    con.close()


def test_parse_export_accepts_text_bytes_and_name_only(tmp_path, monkeypatch):
    # 文本
    out = tag_restore.parse_export('{"tags":[{"name":"甲","media_ids":[1]}]}')
    assert out == [("甲", [1])]
    # 字节（UTF-8 with BOM）
    out2 = tag_restore.parse_export(("\ufeff" + '{"tags":[{"name":"甲","media_ids":[2]}]}').encode("utf-8"))
    assert out2 == [("甲", [2])]
    # 纯名称列表
    assert tag_restore.parse_export({"tags": ["甲", "乙"]}) == [("甲", []), ("乙", [])]


def test_restore_from_file(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    con.close()
    p = tmp_path / "export.json"
    p.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")

    st = tag_restore.restore_from_file(str(p), dry_run=True)
    assert st["links_added"] == 3 and st["links_after"] == 0
    st2 = tag_restore.restore_from_file(str(p), dry_run=False)
    assert st2["links_added"] == 3 and st2["links_after"] == 3
