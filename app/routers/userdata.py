"""个人化数据：观看状态 / 评分 / 收藏 / 备注。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import config as cfg_mod
from ..db import get_db
from ..services import ratings
from ..services.ratings import RATING_MAX

router = APIRouter(prefix="/api", tags=["userdata"])

STATUSES = {"未看", "想看", "在看", "看完"}


class StateIn(BaseModel):
    status: Optional[str] = None
    personal_rating: Optional[float] = Field(None, ge=0, le=RATING_MAX)
    favorite: Optional[bool] = None
    note: Optional[str] = None


def _rating_summary(con, mid: int) -> dict:
    """读回该作品的最新评分口径（列表卡片与详情页共用）。"""
    row = con.execute(
        "SELECT rating_avg, rating_votes, rating_norm FROM media WHERE id=?", (mid,)).fetchone()
    if row is None:
        return {"avg": None, "votes": 0, "display": None}
    avg = row["rating_avg"]
    return {
        "avg": avg,
        "votes": row["rating_votes"] or 0,
        # 无用户评分 → 回退历史/简评分
        "display": avg if avg is not None else row["rating_norm"],
    }


@router.put("/media/{mid}/state")
def set_state(mid: int, body: StateIn, user_id: int | None = None, con=Depends(get_db)):
    """写入观看状态 / 评分 / 收藏 / 备注。

    **每个用户对每个作品只有一条记录**（``watch_state`` 主键 ``(media_id, user_id)``），
    重复评分直接覆盖上一次结果（UPSERT），不会新增行。
    """
    cfg = cfg_mod.load()
    uid = user_id or cfg["default_user_id"]
    if not con.execute("SELECT 1 FROM media WHERE id=?", (mid,)).fetchone():
        from fastapi import HTTPException
        raise HTTPException(404, "not found")
    if body.status and body.status not in STATUSES:
        from fastapi import HTTPException
        raise HTTPException(400, f"status ∈ {sorted(STATUSES)}")

    cur = con.execute(
        "SELECT status, personal_rating, favorite, note FROM watch_state WHERE media_id=? AND user_id=?",
        (mid, uid)).fetchone()
    # personal_rating：显式传了才改（含传 null = 清除）；没传则保持原值
    if "personal_rating" in body.model_fields_set:
        pr = body.personal_rating
    else:
        pr = cur["personal_rating"] if cur else None
    vals = {
        "status": body.status if body.status is not None else (cur["status"] if cur else "未看"),
        "personal_rating": pr,
        "favorite": (1 if body.favorite else 0) if body.favorite is not None else (cur["favorite"] if cur else 0),
        "note": body.note if body.note is not None else (cur["note"] if cur else ""),
    }
    con.execute(
        """INSERT INTO watch_state(media_id,user_id,status,personal_rating,favorite,note,updated_at)
           VALUES(:mid,:uid,:status,:pr,:fav,:note,datetime('now','localtime'))
           ON CONFLICT(media_id,user_id) DO UPDATE SET
             status=excluded.status, personal_rating=excluded.personal_rating,
             favorite=excluded.favorite, note=excluded.note, updated_at=excluded.updated_at""",
        {"mid": mid, "uid": uid, "status": vals["status"],
         "pr": vals["personal_rating"], "fav": vals["favorite"], "note": vals["note"]})
    con.commit()
    # 评分变更 → 立即重算该作品均值（写入路径，单行代价极低）；多端刷新即拿到新分
    ratings.recompute_now(con, [mid])
    con.commit()
    return {"ok": True, "rating": _rating_summary(con, mid)}
