"""媒体查询：列表 / 搜索 / 详情 / 统计 / 标签编辑。"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import config as cfg_mod
from ..db import get_db
from ..services.tagdict import TAG_LIMIT
from ..services.metadata_provider import MAX_TAG_LEN
from ..core.media_helpers import media_or_404

router = APIRouter(prefix="/api", tags=["media"])

STATUSES = {"未看", "想看", "在看", "看完"}


class TagIn(BaseModel):
    name: str


def _base_select(user_id: int):
    return f"""
      SELECT m.*, ws.status, ws.personal_rating, ws.favorite, ws.note
      FROM media m
      LEFT JOIN watch_state ws
        ON ws.media_id = m.id AND ws.user_id = ?
    """


def _filters(q, category, year_from, year_to, month, studio, rating_min, status,
             favorite, tag="", tags=None):
    where = []
    params = []
    if category:
        where.append("m.category = ?")
        params.append(category)
    if year_from is not None and year_to is not None:
        where.append("m.year BETWEEN ? AND ?")
        params += [int(year_from), int(year_to)]
    else:
        if year_from is not None:
            where.append("m.year >= ?")
            params.append(int(year_from))
        if year_to is not None:
            where.append("m.year <= ?")
            params.append(int(year_to))
    if month:
        where.append("CAST(strftime('%m', m.publish_date) AS INTEGER) = ?")
        params.append(int(month))
    if studio:
        where.append("m.studio = ?")
        params.append(studio)
    if rating_min is not None:
        where.append("m.rating_norm >= ?")
        params.append(rating_min)
    if status:
        where.append("ws.status = ?")
        params.append(status)
    if favorite:
        where.append("ws.favorite = 1")
    # 标签：支持多选，命中任意一个即显示（OR）
    name_list = []
    for raw in ([tag] if tag else []) + list(tags or []):
        nm = (raw or "").strip()
        if nm and nm not in name_list:
            name_list.append(nm)
    if name_list:
        ph = ",".join("?" * len(name_list))
        where.append(
            "EXISTS (SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id "
            f"WHERE mt.media_id=m.id AND t.name IN ({ph}))")
        params += name_list
    if q:
        like = f"%{q}%"
        where.append("(m.title LIKE ? ESCAPE '\\' OR m.title_jp LIKE ? ESCAPE '\\' OR m.studio LIKE ? OR CAST(m.id AS TEXT) = ?)")
        params += [like, like, like, q.strip()]
    return where, params


@router.get("/media")
def list_media(
    q: str = "", page: int = 1, size: int = Query(30, le=200),
    category: str = "", year_from: int = None, year_to: int = None,
    month: int = None, studio: str = "", rating_min: float = None,
    status: str = "", favorite: int = 0, sort: str = "score", tag: str = "",
    tags: str = "", user_id: int | None = None, con=Depends(get_db),
):
    cfg = cfg_mod.load()
    uid = user_id or cfg["default_user_id"]
    tag_names = [x for x in (tags or "").split(",")][:50]  # 英文逗号分隔的多选标签（OR）
    where, params = _filters(q, category, year_from, year_to, month, studio,
                             rating_min, status, favorite, tag, tag_names)
    base = _base_select(uid)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order = {
        "score": "m.rating_norm IS NULL, COALESCE(m.rating_norm,0) DESC",
        "score_asc": "COALESCE(m.rating_norm,0) ASC",
        "date_desc": "m.publish_date DESC",
        "date_asc": "m.publish_date ASC",
        "title": "m.title COLLATE NOCASE ASC",
        "added": "m.created_at DESC",
        "fav": "ws.favorite DESC, m.rating_norm IS NULL, COALESCE(m.rating_norm,0) DESC",
        "fav_asc": "ws.favorite ASC, COALESCE(m.rating_norm,0) DESC",
    }.get(sort, "m.rating_norm IS NULL, COALESCE(m.rating_norm,0) DESC")

    total = con.execute(f"SELECT COUNT(*) FROM (SELECT m.id FROM media m LEFT JOIN watch_state ws ON ws.media_id=m.id AND ws.user_id=? {where_sql})",
                        [uid, *params]).fetchone()[0]
    rows = con.execute(
        f"{base} {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        [uid, *params, size, (page - 1) * size]).fetchall()

    items = [serialize(r) for r in rows]
    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/media/{mid}")
def media_detail(mid: int, user_id: int | None = None, con=Depends(get_db)):
    cfg = cfg_mod.load()
    uid = user_id or cfg["default_user_id"]
    row = con.execute(_base_select(uid) + " WHERE m.id=?", (uid, mid)).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    d = serialize(row)
    tags = con.execute(
        "SELECT t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=?",
        (mid,)).fetchall()
    d["tags"] = [t["name"] for t in tags]
    d["meta"] = json.loads(d.get("meta") or "{}")
    fav_cnt = con.execute(
        "SELECT COUNT(*) c FROM watch_state WHERE media_id=? AND favorite=1", (mid,)).fetchone()["c"]
    d["favorite_count"] = fav_cnt
    return d




def _tag_names(con, mid: int) -> list:
    rows = con.execute(
        "SELECT t.name FROM tags t JOIN media_tags mt ON mt.tag_id=t.id "
        "WHERE mt.media_id=? ORDER BY t.name", (mid,)).fetchall()
    return [r["name"] for r in rows]


def _normalize_tag(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()[:MAX_TAG_LEN]


def _mark_tagedited(con, mid: int) -> None:
    """把 tags 记入 edited_fields，使联网补全不覆盖手工标签。"""
    m = media_or_404(con, mid)
    try:
        edited = set(json.loads(m["edited_fields"] or "[]"))
    except ValueError:
        edited = set()
    if "tags" not in edited:
        edited.add("tags")
        con.execute(
            "UPDATE media SET edited_fields=?, updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(sorted(edited), ensure_ascii=False), mid))


@router.get("/media/{mid}/related")
def related_media(mid: int, limit: int = 8, con=Depends(get_db)):
    """相同 tag 作品推荐；不足则随机补足（供播放页轮播）。"""
    media_or_404(con, mid)
    tag_ids = [r["tag_id"] for r in con.execute(
        "SELECT tag_id FROM media_tags WHERE media_id=?", (mid,)).fetchall()]
    out = []
    if tag_ids:
        ph = ",".join("?" * len(tag_ids))
        rows = con.execute(
            f"""SELECT m.id, m.title, m.poster_path, COUNT(*) c
                FROM media m JOIN media_tags mt ON mt.media_id=m.id
                WHERE m.id != ? AND mt.tag_id IN ({ph})
                GROUP BY m.id ORDER BY c DESC, RANDOM() LIMIT ?""",
            [mid, *tag_ids, limit]).fetchall()
        out = [dict(r) for r in rows]
    if len(out) < limit:
        exclude = {mid} | {r["id"] for r in out}
        ph = ",".join("?" * len(exclude))
        need = limit - len(out)
        rows = con.execute(
            f"SELECT id, title, poster_path FROM media WHERE id NOT IN ({ph}) ORDER BY RANDOM() LIMIT ?",
            [*exclude, need]).fetchall()
        out += [dict(r) for r in rows]
    return {"items": out[:limit]}


@router.get("/media/{mid}/recommend")
def recommend_media(mid: int, per_page: int = 12, pages: int = 3,
                    seed: int | None = None, user_id: int | None = None,
                    con=Depends(get_db)):
    """播放页底部轮播推荐：多因子加权打分 + 分页（默认每页 12 个 = 2 行 × 6 列）。

    打分因子与编排策略见 ``app/services/recommend.py`` 顶部说明。
    ``seed`` 传入不同值即可得到「换一批」效果；省略则每次随机。
    """
    cfg = cfg_mod.load()
    uid = user_id or cfg["default_user_id"]
    media_or_404(con, mid)
    from ..services.recommend import recommend
    return recommend(con, mid, uid=uid, per_page=per_page, pages=pages, seed=seed)


@router.get("/tags")
def list_tags(con=Depends(get_db)):
    """全部标签（含暂无关联者）+ 使用数，供页眉「标签筛选气泡」分栏展示。

    按使用数降序、再按名称排序；仅返回标签名与计数。
    """
    rows = con.execute(
        "SELECT t.name, COUNT(mt.media_id) c FROM tags t "
        "LEFT JOIN media_tags mt ON mt.tag_id=t.id "
        "GROUP BY t.id ORDER BY c DESC, t.name").fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/tagdict")
def list_tagdict():
    """返回题材词典的规范 tag 列表（供详情抽屉添加标签时联想）。"""
    from ..services.tagdict import TAG_ENTRIES
    seen = []
    for _pat, tag in TAG_ENTRIES:
        if tag not in seen:
            seen.append(tag)
    return {"items": seen}


@router.post("/media/{mid}/tags")
def add_tag(mid: int, body: TagIn, con=Depends(get_db)):
    media_or_404(con, mid)
    name = _normalize_tag(body.name)
    if not name:
        raise HTTPException(400, "标签不能为空")
    current = _tag_names(con, mid)
    if name in current:
        return {"ok": True, "tags": current}
    if len(current) >= TAG_LIMIT:
        raise HTTPException(400, f"每个作品最多 {TAG_LIMIT} 个标签")
    con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
    tid = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
    con.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES(?,?)", (mid, tid))
    _mark_tagedited(con, mid)
    con.commit()
    return {"ok": True, "tags": _tag_names(con, mid)}


@router.delete("/media/{mid}/tags")
def remove_tag(mid: int, body: TagIn, con=Depends(get_db)):
    media_or_404(con, mid)
    name = _normalize_tag(body.name)
    if name:
        con.execute(
            "DELETE FROM media_tags WHERE media_id=? AND tag_id=(SELECT id FROM tags WHERE name=?)",
            (mid, name))
    _mark_tagedited(con, mid)
    con.commit()
    return {"ok": True, "tags": _tag_names(con, mid)}


@router.get("/stats")
def stats(user_id: int | None = None, con=Depends(get_db)):
    cfg = cfg_mod.load()
    uid = user_id or cfg["default_user_id"]
    total = con.execute("SELECT COUNT(*) c FROM media").fetchone()["c"]
    # 分类来自分类字典（与扫描可选分类、作品管理共用同一份数据）；
    # 顺带把 media 中已出现但字典缺失的分类补齐，保证下拉与筛选始终一致。
    from .. import db as db_mod
    db_mod.sync_categories(con)
    con.commit()
    categorized = con.execute(
        "SELECT c.name AS category, COUNT(m.id) c FROM categories c "
        "LEFT JOIN media m ON m.category = c.name GROUP BY c.id ORDER BY c.name").fetchall()
    years = con.execute("SELECT year, COUNT(*) c FROM media WHERE year IS NOT NULL GROUP BY year ORDER BY year").fetchall()
    months = con.execute(
        "SELECT CAST(strftime('%m', publish_date) AS INTEGER) month, COUNT(*) c "
        "FROM media WHERE publish_date IS NOT NULL GROUP BY month ORDER BY month").fetchall()
    by_state = con.execute(
        "SELECT COALESCE(ws.status,'未看') st, COUNT(*) c FROM media m LEFT JOIN watch_state ws ON ws.media_id=m.id AND ws.user_id=? GROUP BY st",
        (uid,)).fetchall()
    fav = con.execute(
        "SELECT COUNT(*) c FROM media m JOIN watch_state ws ON ws.media_id=m.id AND ws.user_id=? WHERE ws.favorite=1",
        (uid,)).fetchone()["c"]
    avg = con.execute("SELECT AVG(rating_norm) a, SUM(rating_norm IS NOT NULL) n FROM media WHERE rating_norm IS NOT NULL").fetchone()
    tags = con.execute(
        "SELECT t.name, COUNT(mt.media_id) c FROM tags t "
        "JOIN media_tags mt ON mt.tag_id=t.id "
        "GROUP BY t.id HAVING c > 0 ORDER BY c DESC, t.name").fetchall()
    return {
        "total": total,
        "categories": [dict(x) for x in categorized],
        "years": [dict(x) for x in years],
        "months": [dict(x) for x in months],
        "watch_state": [dict(x) for x in by_state],
        "favorites": fav,
        "rating_avg": round(avg["a"], 2) if avg["a"] is not None else None,
        "rating_count": avg["n"],
        "tags": [dict(x) for x in tags],
    }


def serialize(r):
    d = dict(r)
    for k in ("status", "personal_rating", "favorite", "note"):
        d.pop(k, None)
    d["user"] = {
        "status": r["status"] or "未看",
        "personal_rating": r["personal_rating"],
        "favorite": bool(r["favorite"]),
        "note": r["note"],
    }
    # 封面模式：video_frame = 用视频预览帧当封面（前端直接渲染视频帧，不存储图片）
    try:
        d["cover_mode"] = (json.loads(d.get("meta") or "{}") or {}).get("cover_mode")
    except (ValueError, TypeError):
        d["cover_mode"] = None
    return d