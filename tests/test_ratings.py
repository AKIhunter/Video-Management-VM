"""评分系统（0~10 分制）：量纲迁移、均值结算、空闲重算。

全部使用临时库与自造数据，不接触真实库。
断言只涉及 id / 数值 / 计数，不读取作品标题与标签文本。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import ratings  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch, init=True):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    if init:
        db.init(con)
    return con


def _add_media(con, mid, rating_norm=None, category="视频"):
    con.execute(
        "INSERT INTO media(id, category, title, file_path, rating_norm) VALUES(?,?,?,?,?)",
        (mid, category, f"t{mid}", f"/tmp/v-{mid}.mp4", rating_norm))
    con.commit()


def _rate(con, mid, user_id, val):
    con.execute(
        "INSERT INTO watch_state(media_id,user_id,personal_rating) VALUES(?,?,?) "
        "ON CONFLICT(media_id,user_id) DO UPDATE SET personal_rating=excluded.personal_rating",
        (mid, user_id, val))
    con.commit()


# ---------------- 历史量纲迁移（0~5 → 0~10） ----------------
def test_scale_migration_doubles_and_clamps(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1, rating_norm=4.0)
    _add_media(con, 2, rating_norm=6.0)      # 旧值可能超过 5，×2 后需封顶 10
    _rate(con, 1, 1, 3.0)
    con.execute("DELETE FROM settings WHERE key='rating_scale_v2'")
    con.commit()

    db.init(con)                              # 重跑 init 触发一次性迁移

    assert con.execute("SELECT rating_norm FROM media WHERE id=1").fetchone()[0] == 8.0
    assert con.execute("SELECT rating_norm FROM media WHERE id=2").fetchone()[0] == 10.0
    assert con.execute(
        "SELECT personal_rating FROM watch_state WHERE media_id=1").fetchone()[0] == 6.0
    assert db.get_setting(con, "rating_scale_v2") == "1"


def test_scale_migration_is_idempotent(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1, rating_norm=4.0)
    assert con.execute("SELECT rating_norm FROM media WHERE id=1").fetchone()[0] == 4.0
    db.init(con)
    db.init(con)                              # 已置标记 → 不再翻倍
    assert con.execute("SELECT rating_norm FROM media WHERE id=1").fetchone()[0] == 4.0


# ---------------- 用户评分均值 ----------------
def test_avg_over_users(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1)
    _rate(con, 1, 1, 8.0)
    _rate(con, 1, 2, 6.0)
    db.recompute_rating_avg(con, [1])
    row = con.execute("SELECT rating_avg, rating_votes FROM media WHERE id=1").fetchone()
    assert row["rating_avg"] == 7.0
    assert row["rating_votes"] == 2


def test_repeat_rating_overwrites_not_adds(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1)
    _rate(con, 1, 1, 5.0)
    _rate(con, 1, 1, 9.0)                     # 同一用户重复评分 → 覆盖
    db.recompute_rating_avg(con, [1])
    row = con.execute("SELECT rating_avg, rating_votes FROM media WHERE id=1").fetchone()
    assert row["rating_votes"] == 1
    assert row["rating_avg"] == 9.0


def test_avg_null_when_all_cleared(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1, rating_norm=7.0)
    _rate(con, 1, 1, 9.0)
    db.recompute_rating_avg(con, [1])
    assert con.execute("SELECT rating_avg FROM media WHERE id=1").fetchone()[0] == 9.0
    con.execute("UPDATE watch_state SET personal_rating=NULL WHERE media_id=1")
    con.commit()
    db.recompute_rating_avg(con, [1])
    row = con.execute("SELECT rating_avg, rating_votes FROM media WHERE id=1").fetchone()
    assert row["rating_avg"] is None            # 回退历史分由读取侧的 COALESCE 负责
    assert row["rating_votes"] == 0


# ---------------- 空闲结算 ----------------
def test_stale_detection_and_settle(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1)
    _rate(con, 1, 1, 4.0)
    assert ratings.stale_media_ids(con) == [1]  # 从未结算 → 过期
    assert ratings.settle_once(con) == 1
    assert ratings.stale_media_ids(con) == []   # 结算后不再过期
    assert ratings.settle_once(con) == 0
    assert con.execute("SELECT rating_avg FROM media WHERE id=1").fetchone()[0] == 4.0


def test_stale_after_repeat_rating(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1)
    _rate(con, 1, 1, 4.0)
    ratings.settle_once(con)
    _rate(con, 1, 1, 10.0)
    con.execute("UPDATE watch_state SET updated_at=datetime('now','localtime','+1 second') "
                "WHERE media_id=1")
    con.commit()
    assert ratings.stale_media_ids(con) == [1]
    ratings.settle_once(con)
    assert con.execute("SELECT rating_avg FROM media WHERE id=1").fetchone()[0] == 10.0


def test_settle_skips_while_job_running(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add_media(con, 1)
    _rate(con, 1, 1, 4.0)

    class _FakeJob:
        is_running = True

    monkeypatch.setattr(ratings.jobs, "current", lambda: _FakeJob())
    assert ratings.settle_once(con) == 0        # 有长任务 → 不动
    monkeypatch.setattr(ratings.jobs, "current", lambda: None)
    assert ratings.settle_once(con) == 1        # 空闲 → 结算
