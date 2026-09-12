"""本机简评打标：从磁盘「简评」txt 的描述文本中按题材词典提取 tag（每作品 ≤ tagdict.TAG_LIMIT）。

临时方案：不联网、不改 edited_fields（保留后续联网补全可继续优化 tags）。
只写 media_tags 并把来源记录进 meta.auto_tags。
也对外暴露 collect_entries / tags_for_title，供联网补全在无联网候选时做 fallback 打标。
"""
import datetime
import json
import os
import re

from .. import config as cfg_mod
from .. import db
from .tagdict import TAG_LIMIT, pick_tags
from .parser import normalize_title
from ..core.fsutils import find_jianping_files, read_utf8

_TITLE_MARK = re.compile(r"(《[^》]+》)")






def _entry_chunks(text: str):
    """按《书名号标题》切分，产出 (title, 描述段文本)。"""
    tokens = _TITLE_MARK.split(text or "")
    cur = None
    chunk = ""
    for i, tok in enumerate(tokens):
        if i % 2 == 1:
            if cur is not None:
                yield cur, chunk
            cur = tok.strip("《》")
            chunk = ""
        else:
            chunk += tok
    if cur is not None:
        yield cur, chunk


def _collect_entries(roots):
    """全盘简评条目：[(norm_title, 原文标题, 描述段文本), ...]。"""
    out = []
    for txt in find_jianping_files(roots):
        for title, chunk in _entry_chunks(read_utf8(txt)):
            nt = normalize_title(title)
            if nt:
                out.append((nt, title, chunk))
    return out


def _set_tags(con, mid, tags):
    con.execute("DELETE FROM media_tags WHERE media_id=?", (mid,))
    for name in tags:
        name = (name or "").strip()
        if not name:
            continue
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES(?,?)", (mid, tid))


def run_tag_backfill(job) -> dict:
    """对库中每部作品：匹配简评条目 → 题材词典打标（≤TAG_LIMIT）。可取消、分块提交。

    job.meta.force=True 时忽略 edited_fields 保护（仅用于误覆盖后的恢复场景）。
    """
    cfg = cfg_mod.load()
    filter_cats = set(cfg["category_filter"])
    force = bool(getattr(job, "meta", {}).get("force"))
    con = db.connect()
    db.init(con)
    try:
        entries = _collect_entries(cfg["roots"])
        where = f"category IN ({','.join('?'*len(filter_cats))})"
        rows = con.execute(
            f"SELECT id, title, meta, edited_fields FROM media WHERE {where}",
            tuple(filter_cats)).fetchall()
        total = len(rows)
        job.begin(total)
        tagged = skipped = protected = done = 0
        for i, r in enumerate(rows):
            if job.stop.is_set():
                break
            # 尊重手工编辑：已人工编辑过 tags 的作品不再被临时打标覆盖
            try:
                edited = set(json.loads(r["edited_fields"] or "[]"))
            except ValueError:
                edited = set()
            if "tags" in edited and not force:
                protected += 1
                done += 1
                job.tick(done, current=r["title"] or "")
                continue
            m_title = normalize_title(r["title"])
            hits = []
            if m_title:
                for nt, _orig, chunk in entries:
                    if nt and len(m_title) >= 2 and (nt in m_title or m_title in nt):
                        hits.append(chunk)
            tags = pick_tags(hits)  # 默认 limit=TAG_LIMIT
            if tags:
                _set_tags(con, r["id"], tags)
                meta = json.loads(r["meta"] or "{}")
                meta["auto_tags"] = {
                    "source": "jianping",
                    "tags": tags,
                    "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                con.execute(
                    "UPDATE media SET meta=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False), r["id"]))
                tagged += 1
            else:
                skipped += 1
            done += 1
            job.tick(done, current=r["title"] or "")
            if i % 100 == 0:
                con.commit()
        con.commit()
        return {"total": total, "tagged": tagged, "skipped": skipped,
                "protected": protected, "force": force,
                "canceled": bool(job.stop.is_set())}
    finally:
        con.close()


# ---- 对外暴露：供联网补全 fallback 复用 ----
def collect_entries(roots):
    """全盘简评条目：[(norm_title, 原文标题, 描述段文本), ...]。大库较慢，调用方应缓存。"""
    return _collect_entries(roots)


def tags_for_title(entries, title, limit=TAG_LIMIT):
    """给定已收集的简评条目与作品标题，按题材词典产出 fallback tag（≤limit）。

    匹配规则与 run_tag_backfill 一致：normalize_title 双向包含命中后，
    对命中的描述段文本跑 pick_tags。不联网、不落库，仅返回 tag 列表。
    """
    m_title = normalize_title(title or "")
    if not m_title or len(m_title) < 2:
        return []
    hits = []
    for nt, _orig, chunk in entries or []:
        if nt and (nt in m_title or m_title in nt):
            hits.append(chunk)
    return pick_tags(hits, limit=limit) if hits else []
