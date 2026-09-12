"""个人化数据：观看状态 / 评分 / 收藏 / 备注。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import config as cfg_mod
from ..db import get_db

router = APIRouter(prefix="/api", tags=["userdata"])

STATUSES = {"未看", "想看", "在看", "看完"}


class StateIn(BaseModel):
    status: Optional[str] = None
    personal_rating: Optional[float] = Field(None, ge=0, le=6)
    favorite: Optional[bool] = None
    note: Optional[str] = None


@router.put("/media/{mid}/state")
def set_state(mid: int, body: StateIn, user_id: int | None = None, con=Depends(get_db)):
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
    vals = {
        "status": body.status if body.status is not None else (cur["status"] if cur else "未看"),
        "personal_rating": body.personal_rating if body.personal_rating is not None else (cur["personal_rating"] if cur else None),
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
    return {"ok": True}