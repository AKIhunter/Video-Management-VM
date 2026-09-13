"""联网补全工作器：对缺失信息的作品自动回填（尊重 edited_fields）。

补全范围（合并后的「全量联网补全」）：
- scope='missing'：补全库中缺 简介 / 发布年月 / 标签 的作品（依序处理，分块提交，可取消）。
- mid 指定时：仅补全单个作品。
- ids 指定时：仅补全传入 ID 列表的作品（含信息完整者，重点补 tag）。

补全内容：
- 简介：联网候选（本地→百度→media-db 级联），≤500 字。
- 发布年月：文件夹路径派生（最可靠）优先，联网候选 publish_date 兜底；系统计算值，
  不污染 edited_fields，但尊重人工已编辑的 year/publish_date。
- 标签：联网候选经 kinks.filter_kink 过滤（按题材，≤TAG_LIMIT）优先；
  作品完全无标签且联网未命中时，以本机简评打标（tagging.tags_for_title）作 fallback。

edited_fields 保护是铁律：synopsis / tags 已人工编辑则永不覆盖；year / publish_date
人工编辑过的 likewise 跳过。本机简评打标不写入 edited_fields（保留后续联网可继续优化）。
"""
import datetime
import json
import os

from .. import config as cfg_mod
from .. import db
from . import kinks
from . import tagging
from .metadata_provider import (OnlineResolver, synopsis_missing,
                                 validate_candidate)
from .parser import derive_year_from_path
from ..core.media_helpers import edited


def _resolver(source="online"):
    return OnlineResolver()




def _set_tags(con, mid, tags):
    """整组替换某作品的 tag（tags 视为唯一、按名称去重）。"""
    con.execute("DELETE FROM media_tags WHERE media_id=?", (mid,))
    for name in tags:
        name = (name or "").strip()
        if not name:
            continue
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES(?,?)", (mid, tid))


def _needs_completion(row) -> bool:
    """是否需要补全：缺简介、或缺发布年月、或缺标签。"""
    has_syn = (row["synopsis"] or "").strip()
    has_date = (row["publish_date"] or "").strip() and row["year"]
    has_tags = bool(row["has_tags"]) if "has_tags" in row.keys() else None
    return (not has_syn) or (not has_date) or (not has_tags)


def run_completion(job, source="online", mid=None, ids=None, resolver=None):
    """执行补全。job 提供进度与取消。返回统计 dict。"""
    cfg = cfg_mod.load()
    filter_cats = set(cfg["category_filter"])
    con = db.connect()
    db.init(con)
    if resolver is None:
        resolver = _resolver(source)
    missing = []
    # 简评条目懒加载：仅在实际需要 fallback 打标时才全盘收集一次
    _entries_box = []  # [entries] 惰性缓存容器

    def _entries():
        if not _entries_box:
            _entries_box.append(tagging.collect_entries(cfg["roots"]))
        return _entries_box[0]

    try:
        if mid is not None:
            row = con.execute(
                "SELECT m.*, EXISTS(SELECT 1 FROM media_tags mt WHERE mt.media_id=m.id) AS has_tags "
                "FROM media m WHERE m.id=?", (mid,)).fetchone()
            targets = [row] if row else []
            scope = "one"
        elif ids:
            id_list = [int(x) for x in ids if str(x).strip().isdigit()]
            if id_list:
                rows = {r["id"]: r for r in con.execute(
                    "SELECT m.*, EXISTS(SELECT 1 FROM media_tags mt WHERE mt.media_id=m.id) AS has_tags "
                    "FROM media m WHERE m.id IN (%s)" % ",".join("?" * len(id_list)),
                    tuple(id_list)).fetchall()}
            else:
                rows = {}
            targets = [rows[i] for i in id_list if i in rows]
            missing = [i for i in id_list if i not in rows]
            scope = "ids"
        else:
            cat_where = ",".join("?" * len(filter_cats))
            rows = con.execute(
                f"SELECT m.*, EXISTS(SELECT 1 FROM media_tags mt WHERE mt.media_id=m.id) AS has_tags "
                f"FROM media m WHERE m.category IN ({cat_where})",
                tuple(filter_cats)).fetchall()
            targets = [r for r in rows if _needs_completion(r)]
            scope = "missing"
            missing = []

        total = len(targets)
        job.begin(total)
        filled = failed = failed_reason = skipped = done = 0
        date_filled = year_filled = tags_online = tags_local = 0
        first_error = None

        for i, row in enumerate(targets):
            if job.stop.is_set():
                break
            try:
                cand = resolver.resolve({k: row[k] for k in row.keys() if k != "has_tags"})
            except Exception as e:  # noqa: BLE001
                cand = {"error": f"{e}"}
            if job.stop.is_set():
                break

            edited_set = edited(row)
            cand = validate_candidate(cand)  # 强制约束：简介≤500字、tag≤30×≤20字、publish_date 规范化
            wrote = False

            # ① 简介
            if cand.get("synopsis") and not (row["synopsis"] or "").strip() and "synopsis" not in edited_set:
                con.execute("UPDATE media SET synopsis=?, edited_fields=?, "
                            "updated_at=datetime('now','localtime') WHERE id=?",
                            (cand["synopsis"],
                             json.dumps(sorted(edited_set | {"synopsis"}), ensure_ascii=False), row["id"]))
                edited_set.add("synopsis")
                wrote = True

            # ② 发布年月：路径派生优先 → 联网候选兜底（系统计算值，不写 edited_fields）
            need_date = (not (row["publish_date"] or "").strip() or not row["year"]) \
                and "publish_date" not in edited_set and "year" not in edited_set
            if need_date:
                d = derive_year_from_path(row["file_path"] or "") if os.path.exists(row["file_path"] or "") else {}
                new_date = d.get("publish_date") or cand.get("publish_date")
                new_year = d.get("year")
                if new_date:
                    con.execute("UPDATE media SET publish_date=?, updated_at=datetime('now','localtime') WHERE id=?",
                                (new_date, row["id"]))
                    date_filled += 1
                    wrote = True
                    if not row["year"] and new_year:
                        con.execute("UPDATE media SET year=?, updated_at=datetime('now','localtime') WHERE id=?",
                                    (int(new_year), row["id"]))
                        year_filled += 1
                    _stamp_meta(con, row, "completion_date", {"source": "path" if d.get("publish_date") else "web"})
                elif new_year and not row["year"]:
                    con.execute("UPDATE media SET year=?, updated_at=datetime('now','localtime') WHERE id=?",
                                (int(new_year), row["id"]))
                    year_filled += 1
                    wrote = True

            # ③ 标签：联网 kink 优先 → 本机简评打标 fallback（仅作品完全无标签时）
            if "tags" not in edited_set:
                online_tags = kinks.filter_kink(cand.get("tags") or [], limit=kinks.TAG_LIMIT)
                if online_tags:
                    _set_tags(con, row["id"], online_tags)
                    edited_set.add("tags")
                    con.execute("UPDATE media SET edited_fields=?, updated_at=datetime('now','localtime') WHERE id=?",
                                (json.dumps(sorted(edited_set), ensure_ascii=False), row["id"]))
                    tags_online += 1
                    wrote = True
                elif not row["has_tags"]:
                    # 联网未取到题材 tag 且作品无任何标签 → 本机简评打标 fallback
                    local_tags = tagging.tags_for_title(_entries(), row["title"], limit=kinks.TAG_LIMIT)
                    if local_tags:
                        _set_tags(con, row["id"], local_tags)
                        _stamp_meta(con, row, "auto_tags", {
                            "source": "jianping", "tags": local_tags,
                            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        })
                        tags_local += 1
                        wrote = True

            if wrote:
                filled += 1
            elif cand.get("error"):
                failed += 1
                if first_error is None:
                    first_error = cand["error"]
            else:
                skipped += 1

            done += 1
            job.tick(done, current=row["title"])
            if i % 50 == 0:
                con.commit()  # 分块提交，避免单次事务过大

        con.commit()
        return {
            "scope": scope, "total": total, "filled": filled,
            "failed": failed, "failed_reason": first_error,
            "skipped": skipped, "canceled": bool(job.stop.is_set()),
            "missing": missing,
            "source": getattr(resolver, "name", source),
            "date_filled": date_filled, "year_filled": year_filled,
            "tags_online": tags_online, "tags_local": tags_local,
        }
    finally:
        con.close()


def _stamp_meta(con, row, key, value):
    """把 value 合并进 media.meta[key]，不覆盖其它键。"""
    try:
        meta = json.loads(row["meta"] or "{}")
    except (ValueError, TypeError):
        meta = {}
    meta[key] = value
    con.execute("UPDATE media SET meta=?, updated_at=datetime('now','localtime') WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), row["id"]))
