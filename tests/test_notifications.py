"""消息通知系统：_push_notification 落库 + 已读统计 + 摘要格式化。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.routers import admin  # noqa: E402


def _mk_db(tmp_path, monkeypatch):
    monkeypatch.setattr(admin.cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    db.init(con)
    con.close()
    return str(tmp_path / "t.db")


def test_push_notification_persists(tmp_path, monkeypatch):
    _mk_db(tmp_path, monkeypatch)
    admin._push_notification("task_done", "联网补全完成", "total=10 · filled=8")
    admin._push_notification("task_error", "扫描失败", "boom")

    con = db.connect()
    rows = con.execute(
        "SELECT type, title, body, is_read FROM notifications ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["type"] == "task_done" and rows[0]["is_read"] == 0
    assert rows[1]["title"] == "扫描失败"
    # 未读数
    n = con.execute("SELECT COUNT(*) FROM notifications WHERE is_read=0").fetchone()[0]
    assert n == 2
    con.close()


def test_mark_read_clears_unread(tmp_path, monkeypatch):
    _mk_db(tmp_path, monkeypatch)
    admin._push_notification("info", "通知1", "a")
    admin._push_notification("info", "通知2", "b")
    con = db.connect()
    nid = con.execute("SELECT id FROM notifications ORDER BY id LIMIT 1").fetchone()["id"]
    con.execute("UPDATE notifications SET is_read=1 WHERE id=?", (nid,))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM notifications WHERE is_read=0").fetchone()[0]
    assert n == 1
    con.close()


def test_fmt_summary():
    s = admin._fmt_summary("联网补全", {"total": 10, "filled": 8, "skipped": 2})
    assert "联网补全" in s and "total=10" in s and "filled=8" in s
    s2 = admin._fmt_summary("扫描", {"canceled": True})
    assert "已取消" in s2
