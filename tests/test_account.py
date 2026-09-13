"""个人中心：账号等级 / 每日签到 / 收藏夹，与管理中心账号增删改查。

全部使用临时库与自造数据，不接触真实库。
断言只涉及 id / 数值 / 计数 / 结构。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.routers import account, admin  # noqa: E402
from app.services import levels  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    db.init(con)
    return con


# ---------------- 等级规则（纯函数） ----------------
def test_level_of_and_next_need():
    assert levels.level_of(0) == 1
    assert levels.level_of(99) == 1
    assert levels.level_of(100) == 2
    assert levels.level_of(99999) == levels.MAX_LEVEL      # 封顶
    assert levels.next_level_need(0) == 100
    assert levels.next_level_need(30) == 70
    assert levels.next_level_need(100) == 100              # 进入下一级
    assert levels.next_level_need(10 ** 9) == 0            # 满级


def test_checkin_reward_rules():
    assert levels.reward(1) == 10
    assert levels.reward(6) == 10
    assert levels.reward(7) == 30       # 连续第 7 天：+20 额外
    assert levels.reward(14) == 30
    assert levels.reward(8) == 10


def test_streak_continues_and_breaks():
    assert levels.next_streak(None, 0, "2026-09-13") == 1
    assert levels.next_streak("2026-09-12", 5, "2026-09-13") == 6   # 昨天签过 → 连续
    assert levels.next_streak("2026-09-10", 5, "2026-09-13") == 1   # 断签 → 重新计
    assert levels.next_streak("2026-09-13", 4, "2026-09-13") == 4   # 今日已签，不变


def test_can_use_is_a_reserved_hook(monkeypatch):
    assert levels.can_use(1, "not_declared") == (True, 0)           # 未声明默认放行
    monkeypatch.setitem(levels.FEATURE_LEVELS, "comment", 3)
    assert levels.can_use(2, "comment") == (False, 3)
    assert levels.can_use(3, "comment") == (True, 3)


# ---------------- 每日签到 ----------------
def test_checkin_once_per_day_and_level_up(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    r1 = account.checkin(user={"id": 1}, con=con)
    assert r1["ok"] is True and r1["gained"] == 10
    assert r1["checkin_days"] == 1 and r1["checkin_streak"] == 1
    assert r1["points"] == 10 and r1["level"] == 1
    # 同一天重复签到 → 幂等拒绝
    r2 = account.checkin(user={"id": 1}, con=con)
    assert r2["ok"] is False and r2["points"] == 10
    # 模拟「昨天签过」→ 今天再签，连续 +1
    con.execute("UPDATE users SET last_checkin=date('now','-1 day') WHERE id=1")
    con.commit()
    r3 = account.checkin(user={"id": 1}, con=con)
    assert r3["ok"] is True and r3["checkin_streak"] == 2 and r3["points"] == 20


def test_checkin_streak_breaks_after_gap(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    account.checkin(user={"id": 1}, con=con)
    con.execute("UPDATE users SET last_checkin=date('now','-3 day'), checkin_streak=3 WHERE id=1")
    con.commit()
    r = account.checkin(user={"id": 1}, con=con)
    assert r["checkin_streak"] == 1                 # 断签重新计
    assert r["checkin_days"] == 2                   # 累计天数仍增加


# ---------------- 个人收藏夹 ----------------
def test_favorites_lists_only_current_user(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    con.execute("INSERT INTO media(id, category, title, file_path) VALUES(1,'视频','a','/a.mp4')")
    con.execute("INSERT INTO media(id, category, title, file_path) VALUES(2,'视频','b','/b.mp4')")
    con.execute("INSERT INTO users(name, role) VALUES('u2','user')")
    con.execute("INSERT INTO watch_state(media_id,user_id,favorite) VALUES(1,1,1)")
    con.execute("INSERT INTO watch_state(media_id,user_id,favorite) VALUES(2,2,1)")
    con.commit()
    r = account.my_favorites(user={"id": 1}, con=con)
    assert r["total"] == 1 and r["items"][0]["id"] == 1
    assert "file_path" not in r["items"][0]         # 不外露文件路径
    r2 = account.my_favorites(user={"id": 2}, con=con)
    assert [i["id"] for i in r2["items"]] == [2]


# ---------------- 管理中心：账号增删改查 ----------------
def test_user_crud_roundtrip(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    r = admin.create_user(body={"name": "alice", "role": "user"}, _u={}, con=con)
    assert r["ok"] is True and r["user"]["role"] == "user"
    uid = r["user"]["id"]
    admin.update_user(uid, body={"role": "admin"}, _u={}, con=con)
    assert con.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()[0] == "admin"
    admin.delete_user(uid, _u={}, con=con)
    assert con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is None


def test_user_create_rejects_duplicate_and_bad_role(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    admin.create_user(body={"name": "bob"}, _u={}, con=con)
    with pytest.raises(HTTPException) as e:
        admin.create_user(body={"name": "bob"}, _u={}, con=con)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        admin.create_user(body={"name": "carol", "role": "root"}, _u={}, con=con)


def test_default_admin_cannot_be_deleted_or_demoted(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e1:
        admin.delete_user(1, _u={}, con=con)
    assert e1.value.status_code == 400
    with pytest.raises(HTTPException) as e2:
        admin.update_user(1, body={"role": "user"}, _u={}, con=con)
    assert e2.value.status_code == 400


def test_deleting_user_clears_watch_state(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    con.execute("INSERT INTO media(id, category, title, file_path) VALUES(1,'视频','a','/a.mp4')")
    r = admin.create_user(body={"name": "dave"}, _u={}, con=con)
    uid = r["user"]["id"]
    con.execute("INSERT INTO watch_state(media_id,user_id,favorite) VALUES(1,?,1)", (uid,))
    con.commit()
    admin.delete_user(uid, _u={}, con=con)
    assert con.execute(
        "SELECT COUNT(*) FROM watch_state WHERE user_id=?", (uid,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1   # 作品不受影响
