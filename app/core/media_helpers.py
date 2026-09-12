"""媒体相关的公共 helper：edited_fields 解析、媒体 404 查询。

被 backfill / completion / covers / admin / media 共用（消除多处重复实现）。
"""
import json

from fastapi import HTTPException


def edited(row) -> set:
    """解析 media 行的 edited_fields 为 set（人工已编辑的字段名集合）。"""
    try:
        return set(json.loads(row["edited_fields"] or "[]"))
    except ValueError:
        return set()


def media_or_404(con, mid: int):
    """按 id 查媒体，不存在则抛 404。"""
    row = con.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
    if not row:
        raise HTTPException(404, "媒体不存在")
    return row
