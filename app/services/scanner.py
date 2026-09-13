"""扫描器：遍历数据根目录，解析视频文件入库（全量/增量、幂等），并回填简评评分。"""
import datetime
import os
import re
from difflib import SequenceMatcher

from .. import config as cfg_mod
from . import covers as covers_mod
from .. import db
from ..core.fsutils import find_jianping_files, read_utf8
from .parser import (VIDEO_EXTS, derive_year_from_path,
                     normalize_title, parse_jianping, parse_video_filename)

_YEAR_DIR = re.compile(r"^\d{4}年视频$")


def _category_for(path: str) -> str:
    segs = path.replace("\\", "/").split("/")
    for seg in segs:
        if _YEAR_DIR.match(seg):
            return "视频"
    # 未命中视频年份目录：用最顶层目录名作为分类
    return segs[0] if segs else "其他"


def _hash(size, mtime):
    return f"{size}:{int(mtime)}"


DATA_DISK = "medialibrary"      # 数据盘根目录名（大小写不敏感比较）


def source_group(path: str) -> str:
    """「来源组」：按文件路径推导，写入 studio 字段（替换旧「制作组」语义）。

    规则：``上级文件夹/本级文件夹``；上级文件夹是数据盘根（MediaLibrary）时仅记本级。
    例：
      D:\\MediaLibrary\\real_video\\real_video_202503\\a.mp4 → real_video/real_video_202503
      D:\\MediaLibrary\\某分类\\b.mp4                        → 某分类
    """
    norm = (path or "").replace("/", "\\").strip().rstrip("\\")
    if not norm:
        return ""
    # 传入文件路径 → 取其所在目录；传入目录路径（无扩展名）→ 直接用
    dirpath = os.path.dirname(norm)
    if not os.path.splitext(os.path.basename(norm))[1]:
        dirpath = norm
    folder = os.path.basename(dirpath)
    parent = os.path.basename(os.path.dirname(dirpath))
    if not folder:
        return ""
    if not parent or parent.lower() == DATA_DISK:
        return folder
    return f"{parent}/{folder}"




def _iter_chunks(roots):
    """按目录分块产出 (dirpath, [(path, ext), ...])。

    每个目录一个分块，便于「顺序分块 + 可取消」：处理完一个目录才检查取消/回报，
    避免一个长任务夯住整个进程。目录遍历按 os.walk 深度优先，天然同目录连续。
    """
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            batch = []
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext in VIDEO_EXTS:
                    batch.append((os.path.join(dirpath, fn), ext))
            if batch:
                yield dirpath, batch


def _scope_roots(cfg, scope, path):
    """解析扫描范围对应的根目录：full -> 全部 roots；path -> 仅指定目录。"""
    if scope == "path":
        if not path:
            raise ValueError("scope=path 需要提供 path")
        return [os.path.abspath(path)]
    return [os.path.abspath(p) for p in cfg["roots"]]


def do_scan(cfg=None, dry_run=False, progress=None, scope="full", path=None, stop=None,
            category_override=None, stage=None):
    """扫描建索引。

    category_override：指定分类时，本次扫描到的作品一律记入该分类（不再按目录名推断），
    且不受 category_filter 限制（用户显式指定即为准）。
    stage：传入 list 时，``dry_run=True`` 的扫描会把「新增 / 有变化」的候选（**不写库**）
    收集进该列表，供「扫描 → 人工确认 → 导入」两段式流程使用。
    """
    cfg = cfg or cfg_mod.load()
    filter_cats = set(cfg["category_filter"])
    con = db.connect()
    db.init(con)
    lock = db.write_lock()
    lock.acquire()
    try:
        started = datetime.datetime.now()
        note_lines = []
        scanned = added = updated = removed = 0
        found_paths = set()
        canceled = False

        # 现网快照(仅扫描分类；指定分类时把该分类也纳入，避免误判为新增)
        snap_cats = set(filter_cats)
        if category_override:
            snap_cats.add(category_override)
        cat_where = ",".join("?" * len(snap_cats))
        snap = {}
        rows = con.execute(
            f"SELECT id, file_path, scan_hash FROM media WHERE category IN ({cat_where})",
            tuple(snap_cats)).fetchall()
        for r in rows:
            snap[r["file_path"]] = (r["id"], r["scan_hash"])

        chunks = list(_iter_chunks(_scope_roots(cfg, scope, path)))
        total = sum(len(b) for _d, b in chunks)
        cover_index = None  # 惰性构建：只在本扫描首次需要封面时建一次

        def _cover():
            nonlocal cover_index
            if cover_index is None:
                cover_index = covers_mod.build_cover_index(
                    _scope_roots(cfg, scope, path), stop=stop)
            return cover_index

        done = 0
        for dirpath, batch in chunks:
            # 每个目录处理完检查一次取消，保证可随时中断、不至于夯住
            if stop is not None and stop.is_set():
                canceled = True
                note_lines.append("已取消")
                break
            for path_, ext in batch:
                if category_override:
                    category = category_override      # 用户显式指定分类，直接采用
                else:
                    category = _category_for(path_)
                    if category not in filter_cats:
                        continue
                found_paths.add(path_)
                try:
                    st = os.stat(path_)
                except OSError:
                    continue
                h = _hash(st.st_size, st.st_mtime)
                meta = parse_video_filename(os.path.basename(path_))
                meta["meta"]["size"] = st.st_size
                ext = os.path.splitext(path_)[1].lower()

                if path_ in snap and snap[path_][1] == h:
                    continue  # 未变化
                # 海报关联（仅无变化不重算）。先本地精确，再全局封面索引兜底
                poster = covers_mod.resolve(path_, _cover())

                # 文件名无日期时，用文件夹「YYYY年/MM月」派生回填，保证扫描重跑年份不丢
                derived = {}
                if not meta.get("year") or not meta.get("publish_date"):
                    derived = derive_year_from_path(path_)

                data = {
                    "category": category,
                    "title": meta["title"] or os.path.basename(path_),
                    "title_jp": meta.get("title_jp"),
                    # 「来源组」（上级文件夹/本级文件夹，替换旧「制作组」语义；由扫描自动生成）
                    "studio": source_group(path_),
                    "publish_date": meta.get("publish_date") or derived.get("publish_date"),
                    "year": meta.get("year") or derived.get("year"),
                    "subtitle": 1 if meta["subtitle"] else 0,
                    "file_path": path_,
                    "file_size": st.st_size,
                    "file_ext": ext[1:],
                    "duration_sec": None,
                    "poster_path": poster,
                    "meta": json_dumps(meta["meta"]),
                    "scan_hash": h,
                    "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

                if path_ in snap:
                    updated += 1
                    if dry_run:
                        if stage is not None:
                            stage.append({**data, "action": "update", "media_id": snap[path_][0]})
                        continue
                    con.execute(
                        """UPDATE media SET category=:category, title=:title, title_jp=:title_jp,
                           studio=:studio, publish_date=:publish_date, year=:year, subtitle=:subtitle,
                           file_size=:file_size, poster_path=:poster_path, meta=:meta,
                           scan_hash=:scan_hash, updated_at=:updated_at WHERE id=:id""",
                        {**data, "id": snap[path_][0]})
                else:
                    # 兜底：同一文件此前可能被索引到其它分类下（快照未覆盖到），
                    # 此时按 file_path 命中并更新，避免触发 file_path UNIQUE 冲突。
                    exist = con.execute("SELECT id FROM media WHERE file_path=?", (path_,)).fetchone()
                    if exist:
                        updated += 1
                        if dry_run:
                            if stage is not None:
                                stage.append({**data, "action": "update", "media_id": exist["id"]})
                            continue
                        con.execute(
                            """UPDATE media SET category=:category, title=:title, title_jp=:title_jp,
                               studio=:studio, publish_date=:publish_date, year=:year, subtitle=:subtitle,
                               file_size=:file_size, poster_path=:poster_path, meta=:meta,
                               scan_hash=:scan_hash, updated_at=:updated_at WHERE id=:id""",
                            {**data, "id": exist["id"]})
                    else:
                        added += 1
                        if dry_run:
                            if stage is not None:
                                stage.append({**data, "action": "add"})
                            continue
                        cols = list(data.keys())
                        con.execute(
                            f"INSERT INTO media ({','.join(cols)}) VALUES ({','.join(':'+c for c in cols)})",
                            data)
                scanned += 1  # 实际处理（新增/更新）的视频数
                done += 1
            if progress:
                progress(done, total, current=os.path.basename(dirpath))
                if canceled:
                    break

        if not dry_run and not canceled:
            # 移除已消失文件：仅移除「本次扫描范围内」的历史记录。
            # 定点(path)扫描绝不能删除扫描范围之外的媒体。
            scope_roots = _scope_roots(cfg, scope, path)
            in_scope = (lambda p: any(
                os.path.commonpath([os.path.abspath(p), os.path.abspath(rt)]) == os.path.abspath(rt)
                for rt in scope_roots)) if scope == "path" else (lambda p: True)
            for path_, (mid, _h) in snap.items():
                if path_ not in found_paths and in_scope(path_):
                    removed += 1
                    con.execute("DELETE FROM media WHERE id=?", (mid,))
            # 简评评分回填（仅 full 扫描；path 扫描为定点增量，不做全局评分匹配）
            if scope != "path":
                hits, pending = _match_ratings(con, cfg)
                note_lines.append(f"评分回填命中 {hits}，待人工匹配 {len(pending)}")
            else:
                hits, pending = 0, []
                note_lines.append("定点扫描，跳过全局评分回填")
            con.commit()
        else:
            hits = pending = 0

        finished = datetime.datetime.now()
        with con:
            con.execute(
                "INSERT INTO scan_runs(started_at,finished_at,scanned,added,updated,removed,note) "
                "VALUES(?,?,?,?,?,?,?)",
                (started.strftime("%Y-%m-%d %H:%M:%S"),
                 finished.strftime("%Y-%m-%d %H:%M:%S"),
                 scanned, added, updated, removed,
                 "; ".join(note_lines)))
        return {
            "scanned": scanned, "added": added, "updated": updated,
            "removed": removed, "total_found": total,
            "rating_hits": hits,
            "rating_pending": pending if not dry_run else [],
            "dry_run": dry_run,
            "canceled": canceled,
            "scope": scope,
        }
    finally:
        lock.release()
        con.close()


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


def import_staged(items, category, progress=None, stop=None):
    """把暂存清单真正写入索引（人工点「导入」后调用）。

    - 分类一律用**用户显式指定**的 ``category``（防呆：不允许「自动（按目录名）」）。
    - 导入前对每条做**二次校验**：文件仍存在且 ``size:mtime`` 指纹与扫描时一致，
      否则跳过（提示重新扫描），绝不把过期路径写进索引。
    - 不做「移除已消失文件」——那是删除操作，与导入解耦，需要清理时用作品管理。
    """
    cfg = cfg_mod.load()
    con = db.connect()
    db.init(con)
    lock = db.write_lock()
    lock.acquire()
    try:
        added = updated = skipped = 0
        total = len(items)
        done = 0
        for it in items:
            if stop is not None and stop.is_set():
                break
            fp = it.get("file_path") or ""
            try:
                st = os.stat(fp)
            except OSError:
                skipped += 1           # 文件已不在磁盘 → 不导入
            else:
                if _hash(st.st_size, st.st_mtime) != it.get("scan_hash"):
                    skipped += 1       # 扫描之后文件又变了 → 提示重新扫描
                else:
                    data = {k: v for k, v in it.items() if k not in ("action", "media_id")}
                    data["category"] = category
                    # 来源组以**导入时**的当前路径重算（扫描到导入之间文件可能被移动过）
                    data["studio"] = source_group(fp)
                    data["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    exist = con.execute("SELECT id FROM media WHERE file_path=?", (fp,)).fetchone()
                    if exist:
                        con.execute(
                            """UPDATE media SET category=:category, title=:title, title_jp=:title_jp,
                               studio=:studio, publish_date=:publish_date, year=:year, subtitle=:subtitle,
                               file_size=:file_size, poster_path=:poster_path, meta=:meta,
                               scan_hash=:scan_hash, updated_at=:updated_at WHERE id=:id""",
                            {**data, "id": exist["id"]})
                        updated += 1
                    else:
                        cols = list(data.keys())
                        con.execute(
                            f"INSERT INTO media ({','.join(cols)}) VALUES ({','.join(':'+c for c in cols)})",
                            data)
                        added += 1
            done += 1
            if progress and (done % 25 == 0 or done == total):
                progress(done, total, current=it.get("title"))
        # 分类登记进字典（与页面筛选 / 作品管理共用同一份分类数据）
        db.sync_categories(con)
        con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (category,))
        # 简评评分回填（与全量扫描一致；导入可能是大批量新增）
        hits, pending = _match_ratings(con, cfg)
        con.commit()
        return {"added": added, "updated": updated, "skipped": skipped,
                "total": total, "canceled": bool(stop is not None and stop.is_set()),
                "rating_hits": hits, "rating_pending": pending}
    finally:
        lock.release()
        con.close()


def _match_ratings(con, cfg):
    """把简评评分回填到已入库 media。幂等：已链接的「来源文件+归一化标题」不再重复匹配。"""
    import json
    cfg_filter = set(cfg["category_filter"])
    media_rows = con.execute(
        f"SELECT id, title FROM media WHERE category IN ({','.join(['?']*len(cfg_filter))})",
        tuple(cfg_filter)).fetchall()
    target = [(r["id"], normalize_title(r["title"])) for r in media_rows]
    seen = {r["id"] for r in con.execute(
        "SELECT id FROM media WHERE rating_norm IS NOT NULL").fetchall()}

    # 已记录过匹配的来源（防重扫时把同一简评条目链到别的文件）
    existing_keys = set()
    for r in con.execute("SELECT meta FROM media WHERE rating_norm IS NOT NULL"):
        try:
            mm = json.loads(r["meta"] or "{}")
            if mm.get("rating_key"):
                existing_keys.add(mm["rating_key"])
        except ValueError:
            pass

    pending = []
    hits = 0
    for txt in find_jianping_files(cfg["roots"]):
        rel = os.path.relpath(txt, cfg_mod.BASE)
        try:
            text = read_utf8(txt)
        except OSError:
            continue
        for e in parse_jianping(text):
            nt = normalize_title(e["title"])
            if not nt:
                continue
            key = rel + "|" + nt
            if key in existing_keys:
                continue
            mid = None
            for i, (m_id, m_t) in enumerate(target):
                if i in seen:
                    continue
                if nt in m_t or m_t in nt:
                    mid = m_id
                    break
            if mid is None:
                best_m, best_r = None, 0.0
                for m_id, m_t in target:
                    if m_id in seen:
                        continue
                    r = SequenceMatcher(None, nt, m_t).ratio()
                    if r > best_r:
                        best_r, best_m = r, m_id
                if best_m is not None and best_r >= 0.85:
                    mid = best_m
            if mid is not None:
                cur_meta = json.loads((con.execute(
                    "SELECT meta FROM media WHERE id=?", (mid,)).fetchone()["meta"] or "{}"))
                cur_meta["rating_key"] = key
                con.execute(
                    "UPDATE media SET rating_norm=?, rating_raw=?, rating_source=?, meta=? WHERE id=?",
                    (e["score"], e["raw_score"][:200], rel,
                     json.dumps(cur_meta, ensure_ascii=False), mid))
                seen.add(mid)
                existing_keys.add(key)
                hits += 1
            else:
                pending.append({"title": e["title"], "score": e["score"], "source": txt})
    return hits, pending


    return ""