"""播放：Range 流式 / 本地默认播放器回退 / 守卫建议 / 下载。"""
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import config as cfg_mod
from ..services import streaming
from ..db import get_db

router = APIRouter(prefix="/api", tags=["playback"])


def _get_media(mid, con):
    row = con.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return row


@router.get("/play/{mid}")
def play(mid: int, request: Request, con=Depends(get_db)):
    cfg = cfg_mod.load()
    m = _get_media(mid, con)
    if not m["file_path"] or not os.path.exists(m["file_path"]):
        raise HTTPException(410, "文件不存在")
    return streaming.stream_file(m["file_path"], request)


@router.get("/play/{mid}/meta")
def play_meta(mid: int, con=Depends(get_db)):
    cfg = cfg_mod.load()
    m = _get_media(mid, con)
    return {
        "media_id": mid,
        "path": m["file_path"],
        "guard": streaming.guard_advice(m, cfg),
        "filename": os.path.basename(m["file_path"] or ""),
    }


@router.post("/open-local/{mid}")
def open_local(mid: int, con=Depends(get_db)):
    cfg = cfg_mod.load()
    m = _get_media(mid, con)
    # default_player_open 关闭时拒绝（作为安全开关）
    if not cfg.get("default_player_open", True):
        return {"ok": False, "reason": "default_player_open 已关闭"}
    if not m["file_path"]:
        return {"ok": False, "reason": "无文件路径"}
    ok = streaming.open_local(m["file_path"])
    return {"ok": ok}


@router.get("/download/{mid}")
def download(mid: int, con=Depends(get_db)):
    m = _get_media(mid, con)
    if not m["file_path"] or not os.path.exists(m["file_path"]):
        raise HTTPException(410, "文件不存在")
    from starlette.responses import FileResponse
    return FileResponse(m["file_path"], filename=os.path.basename(m["file_path"]))