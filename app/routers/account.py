"""个人中心：当前账号信息 / 每日签到 / 个人收藏夹。"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from ..authz import current_user
from ..db import get_db
from ..services import levels

log = logging.getLogger("vm.account")

router = APIRouter(prefix="/api", tags=["account"])


def _account(con, user: dict) -> dict:
    """当前账号完整信息（等级 / 成长值 / 签到状态）。"""
    row = con.execute(
        "SELECT id, name, role, points, checkin_days, checkin_streak, last_checkin "
        "FROM users WHERE id=?", (user["id"],)).fetchone()
    if row is None:
        raise HTTPException(401, "当前用户不存在")
    points = row["points"] or 0
    lv = levels.level_of(points)
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "points": points,
        "level": lv,
        "next_level_need": levels.next_level_need(points),
        "checkin_days": row["checkin_days"] or 0,
        "checkin_streak": row["checkin_streak"] or 0,
        "last_checkin": row["last_checkin"],
        "checked_in_today": row["last_checkin"] == levels.today(),
        "max_level": levels.MAX_LEVEL,
        # 等级权益预留位（levels.FEATURE_LEVELS，当前为空 = 未启用任何限制）
        "perks": [{"feature": k, "level": v} for k, v in sorted(levels.FEATURE_LEVELS.items())],
    }


@router.get("/me")
def me(user: dict = Depends(current_user), con=Depends(get_db)):
    """当前账号（含角色 / 等级 / 签到状态）。前端据此决定「白屏管理」入口与个人中心展示。"""
    return _account(con, user)


@router.post("/me/checkin")
def checkin(user: dict = Depends(current_user), con=Depends(get_db)):
    """每日签到：**每天一次**，+成长值（连续签到每满 7 天有额外奖励），累计到账号等级。"""
    row = con.execute(
        "SELECT points, checkin_days, checkin_streak, last_checkin FROM users WHERE id=?",
        (user["id"],)).fetchone()
    if row is None:
        raise HTTPException(401, "当前用户不存在")
    today = levels.today()
    if row["last_checkin"] == today:
        acc = _account(con, user)
        return {"ok": False, "msg": "今天已经签到过啦", **acc}
    streak = levels.next_streak(row["last_checkin"], row["checkin_streak"], today)
    gained = levels.reward(streak)
    points = (row["points"] or 0) + gained
    con.execute(
        """UPDATE users SET points=?, checkin_days=checkin_days+1,
           checkin_streak=?, last_checkin=? WHERE id=?""",
        (points, streak, today, user["id"]))
    con.commit()
    log.info("账号 %s 签到：+%s 成长值（连续 %s 天）", user.get("name") or user["id"], gained, streak)
    acc = _account(con, user)
    return {"ok": True, "gained": gained, "streak": streak, "msg": f"签到成功，成长值 +{gained}", **acc}


@router.get("/me/favorites")
def my_favorites(user: dict = Depends(current_user), con=Depends(get_db)):
    """个人收藏夹：当前账号收藏的作品（点击进详情）。"""
    rows = con.execute(
        """SELECT m.id, m.title, m.title_jp, m.year, m.publish_date,
                  COALESCE(m.rating_avg, m.rating_norm) AS rating_display,
                  m.rating_votes, m.poster_path, meta
           FROM watch_state w JOIN media m ON m.id = w.media_id
           WHERE w.user_id = ? AND w.favorite = 1
           ORDER BY w.updated_at DESC, m.id DESC""",
        (user["id"],)).fetchall()
    items = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["meta"] or "{}") or {}
        except (ValueError, TypeError):
            meta = {}
        items.append({
            "id": r["id"], "title": r["title"], "title_jp": r["title_jp"],
            "year": r["year"], "publish_date": r["publish_date"],
            "rating_display": r["rating_display"],
            "rating_votes": r["rating_votes"] or 0,
            "cover_mode": meta.get("cover_mode"),
        })
    return {"items": items, "total": len(items)}
