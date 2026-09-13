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
    # 浏览器不便直接播放的格式（avi / wmv / rmvb 等）→ ffmpeg 实时转码为可流式的分片 mp4
    if streaming.needs_transcode(m["file_path"]):
        return streaming.stream_transcode(m["file_path"], request, cfg)
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
        # True = 该格式由服务端实时转码播放（分片 mp4，进度条不可拖动）
        "transcode": streaming.needs_transcode(m["file_path"] or ""),
    }


@router.get("/play/{mid}/related")
def play_related(mid: int, limit: int = 20, con=Depends(get_db)):
    """关联作品：**标题高度相似 + 同目录（文件路径）** 的其它作品。

    用途：播放页右侧栏快速切换播放、当前视频播完自动连播下一个。
    排序：相似度与「同目录」（通常是同系列分卷）加权；相似度过低且不同目录的不返回。
    仅返回 id / 标题 / 年月 / 评分 / 时长等展示字段，不返回文件路径。
    """
    import os as _os
    from difflib import SequenceMatcher

    from ..services.parser import normalize_title

    m = _get_media(mid, con)
    cur_norm = normalize_title(m["title"] or "") or normalize_title(m["title_jp"] or "")
    cur_dir = _os.path.dirname(m["file_path"] or "")
    rows = con.execute(
        """SELECT id, title, title_jp, year, publish_date, file_path,
                  COALESCE(rating_avg, rating_norm) AS rating_display,
                  rating_votes, duration_sec
           FROM media WHERE id != ?""",
        (mid,)).fetchall()
    items = []
    for r in rows:
        norm = normalize_title(r["title"] or "") or normalize_title(r["title_jp"] or "")
        same_dir = bool(cur_dir) and _os.path.dirname(r["file_path"] or "") == cur_dir
        sim = 0.0
        if cur_norm and norm:
            if cur_norm == norm:
                sim = 1.0
            elif cur_norm in norm or norm in cur_norm:
                sim = 0.9
            else:
                sim = SequenceMatcher(None, cur_norm, norm).ratio()
        if not (same_dir or sim >= 0.55):
            continue
        score = sim + (0.35 if same_dir else 0.0)
        items.append({
            "id": r["id"], "title": r["title"], "title_jp": r["title_jp"],
            "year": r["year"], "publish_date": r["publish_date"],
            "rating_display": r["rating_display"], "rating_votes": r["rating_votes"] or 0,
            "duration_sec": r["duration_sec"],
            "same_dir": same_dir, "sim": round(sim, 2), "_score": score,
        })
    items.sort(key=lambda x: -x["_score"])
    for it in items:
        it.pop("_score", None)
    # 附带作品原有标签（播放页关联列表只展示标签，不展示同目录等推导信息）
    tag_map = {}
    ids = [it["id"] for it in items]
    if ids:
        ph = ",".join("?" * len(ids))
        for r in con.execute(
                f"""SELECT mt.media_id, t.name FROM media_tags mt
                    JOIN tags t ON t.id = mt.tag_id
                    WHERE mt.media_id IN ({ph}) ORDER BY t.name""", ids):
            tag_map.setdefault(r["media_id"], []).append(r["name"])
    for it in items:
        it["tags"] = tag_map.get(it["id"], [])
    return {"items": items[:max(1, min(int(limit or 20), 50))], "total": len(items)}


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