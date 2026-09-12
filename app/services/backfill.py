"""数据库信息补全：利用「文件夹已按年份/月份分类」回填缺失的 年份/发布日期/制作组。

这些值由目录路径派生，属系统计算；写入时不污染 edited_fields——仅当管理员在白屏
管理手动编辑过对应字段时才尊重人工值。
"""
import json
import os

from .. import config as cfg_mod
from .. import db
from .parser import derive_year_from_path
from ..core.media_helpers import edited




def run_metadata_backfill(job) -> dict:
    """补齐 media 里缺失的 year / publish_date（可由文件夹路径派生时）。"""
    cfg = cfg_mod.load()
    filter_cats = set(cfg["category_filter"])
    con = db.connect()
    db.init(con)
    try:
        where = f"category IN ({','.join('?'*len(filter_cats))})"
        rows = con.execute(
            f"SELECT id, file_path, year, publish_date, studio, edited_fields FROM media WHERE {where}",
            tuple(filter_cats)).fetchall()
        targets = [r for r in rows
                   if not r["year"] or not r["publish_date"] or not r["studio"]]
        total = len(targets)
        job.begin(total)
        filled_year = filled_date = filled_studio = skipped = done = 0
        for i, r in enumerate(targets):
            if job.stop.is_set():
                break
            edited_set = edited(r)
            upd = []
            vals = []
            d = derive_year_from_path(r["file_path"]) if os.path.exists(r["file_path"]) else {}
            if not r["year"] and d.get("year") and "year" not in edited_set:
                upd.append("year=?")
                vals.append(d["year"])
                filled_year += 1
            if not r["publish_date"] and d.get("publish_date") and "publish_date" not in edited_set:
                upd.append("publish_date=?")
                vals.append(d["publish_date"])
                filled_date += 1
            if not r["studio"] and "studio" not in edited_set:
                # 制作组仅在没有可靠来源时跳过（不猜测）；有路径名可试推一手
                filestem = os.path.basename(r["file_path"])
                from .parser import parse_video_filename
                st = parse_video_filename(filestem).get("studio")
                if st:
                    upd.append("studio=?")
                    vals.append(st)
                    filled_studio += 1
            if upd:
                vals += [r["id"]]
                con.execute(
                    f"UPDATE media SET {', '.join(upd)}, "
                    f"updated_at=datetime('now','localtime') WHERE id=?",
                    vals)
                done_for = True
            else:
                done_for = False
            if not done_for:
                skipped += 1
            done += 1
            job.tick(done, current=r["file_path"] or "")
            if i % 100 == 0:
                con.commit()
        con.commit()
        return {"total": total, "year": filled_year, "publish_date": filled_date,
                "studio": filled_studio, "skipped": skipped,
                "canceled": bool(job.stop.is_set())}
    finally:
        con.close()