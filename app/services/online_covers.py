"""联网封面补全：为磁盘上无封面图的作品，从互联网搜索并下载封面。

每部缺封面作品的处理流程：
  1. 关键词 = 日文原题（无则本地标题）
  2. media-db.example.com 搜索 → 取与关键词关联度最高的帖子
  3. 帖子页提取 og:image（优先）或正文附件图作为候选 URL
  4. 逐候选下载 → PIL 校验（真实图片 + 尺寸 ≥ 200×200）→ 存入 cover_cache/{id}_{safe}.{ext}
  5. 写回 media.poster_path（尊重人工编辑字段）；来源记录进 meta.online_cover

置信度控制：仅当帖子标题与关键词的相似度达到阈值才采用，避免错配封面。
任何网络/解析失败都降级为「未找到」，不抛异常、不阻塞批量任务。
"""
import gzip
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher
from io import BytesIO

from .. import config as cfg_mod
from .. import db
from .metadata_provider import (UA, MediaDbResolver, extract_media_db_results,
                                normalize_keyword)

MIN_WH = 200          # 图片最小边长（过滤缩略图/图标/广告占位图）
MAX_WH_RATIO = 3.5    # 长宽比上限（过滤横条站点头图/banner）
CACHE_DIR = "cover_cache"
MIN_CONF = 0.40       # 帖子标题与关键词的最小相似度，低于则不采用
KW_MAX = 24           # 搜索关键词最大长度（超长标题截取主体部分）

_SIZED_IMG = re.compile(r"-\d+x\d+(\.(?:jpg|jpeg|png|webp))$", re.I)

_OG_IMAGE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', re.I)
_OG_IMAGE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', re.I)
_IMG_SRC = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
_BAD_IMG = re.compile(
    r"(?i)(logo|avatar|icon|blank|spacer|pixel|emoji|1x1|\.gif$|data:|"
    r"cropped-|aj0\d|banner|advert|default|sprite|bg-|header|footer|"
    r"wp-includes|smiley)")
# 图片上传目录形如 wp-content/uploads/2017/06/…，年份可用于与作品年份比对
_UPLOAD_YEAR = re.compile(r"uploads/(\d{4})/")


def _safe_stem(title: str, max_len=40) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title or "").strip(" .")
    return (s[:max_len] or "cover")


def _fetch(url: str, timeout: float = 12.0, referer: str | None = None) -> bytes:
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
    return data


def _extract_image_candidates(html: str) -> list:
    """从帖子页提取候选图 URL：og:image 优先，其次正文 <img>。"""
    out = []
    m = _OG_IMAGE.search(html) or _OG_IMAGE_REV.search(html)
    if m:
        out.append(urllib.parse.unquote(m.group(1).strip()))
    for m in _IMG_SRC.finditer(html):
        u = urllib.parse.unquote(m.group(1).strip())
        if u and not _BAD_IMG.search(u) and u not in out:
            out.append(u)
    return out


def _build_keyword(media_row) -> str:
    """搜索关键词：优先日文原题，其次标题；去掉括号噪声并截断保留主体。"""
    kw = (media_row.get("title_jp") or media_row.get("title") or "").strip()
    kw = re.sub(r"[「」『』“”\"'【】\[\]（）()]", "", kw)
    kw = kw.split("第")[0].strip()          # 取集数/卷数前的标题主体
    kw = kw.split("＃")[0].strip()
    if len(kw) > KW_MAX:
        kw = kw[:KW_MAX]
    return kw


def _clean_post_title(title: str) -> str:
    """清洗帖子标题用于置信度比较：去 [制作组]/【熟】/「生肉」等噪声前缀。"""
    t = re.sub(r"\[[^\]]*\]|【[^】]*】|「[^」]*」", "", title or "")
    t = re.sub(r"(?i)^\s*(熟肉|生肉|中文|官方)?\s*", "", t)
    return t.strip()


def _search_media_db(keyword: str):
    """media-db 搜索一次，返回 (best_title, best_url, conf) 或 (None, None, 0)。"""
    resolver = MediaDbResolver()
    try:
        html_text = _fetch(resolver._search_url(keyword)).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return None, None, 0.0
    results = extract_media_db_results(html_text)
    if not results:
        return None, None, 0.0
    kw = normalize_keyword(keyword)
    best, best_r = None, 0.0
    for item in results:
        t = _clean_post_title(item[0])
        r = SequenceMatcher(None, normalize_keyword(t), kw).ratio() if kw else 0.0
        if r > best_r:
            best_r, best = r, item
    if best is None:
        best = results[0]
    return best[0], best[1], round(best_r, 3)


def _rank_candidates(cands: list, year: int | None) -> list:
    """过滤站点通用图/黑名单图，并按上传年份与作品年份的距离排序（近者优先）。"""
    good = []
    for u in cands:
        if _BAD_IMG.search(u):
            continue
        good.append(u)
    if not year:
        return good
    def key(u):
        m = _UPLOAD_YEAR.search(u)
        if not m:
            return 10 ** 9
        return abs(int(m.group(1)) - year)
    good.sort(key=key)
    return good


def _expand_candidates(cands: list) -> list:
    """WordPress 缩略图 URL 补上原图（去掉 -117x117 等尺寸后缀）。"""
    out = []
    for u in cands:
        out.append(u)
        m = _SIZED_IMG.search(u)
        if m:
            orig = u[:m.start()] + m.group(1)
            if orig not in out:
                out.append(orig)
    return out


def _verify_image(data: bytes):
    """校验字节流是真实图片且尺寸达标；返回 (ok, width, height)。"""
    if len(data) < 4096:
        return False, 0, 0
    try:
        from PIL import Image
        img = Image.open(BytesIO(data))
        w, h = img.size
        img.verify()
    except Exception:  # noqa: BLE001
        return False, 0, 0
    if w < MIN_WH or h < MIN_WH:
        return False, w, h
    if max(w, h) / max(1, min(w, h)) > MAX_WH_RATIO:
        return False, w, h
    return True, w, h


def search_cover(media_row, keyword: str | None = None) -> dict:
    """联网搜索某作品的封面候选。返回 {url, candidates, confidence, source, post_url, title_matched, error}。

    keyword 缺省时由作品标题派生；人工审定可传入自定义关键词重搜。
    """
    full = (keyword or _build_keyword(media_row)).strip()
    kws = [full]
    if len(full) > 12:
        kws.append(full[:12])            # 短关键词重试：长标题易被搜索分词拆散
    year = None
    from .parser import derive_year_from_path
    year = derive_year_from_path(media_row.get("file_path") or "").get("year")

    best_title = best_url = None
    best_conf = 0.0
    for kw in kws:
        t, u, conf = _search_media_db(kw)
        if u and conf > best_conf:
            best_title, best_url, best_conf = t, u, conf
    if not best_url:
        return {"url": None, "candidates": [], "confidence": 0.0, "source": "media-db.example.com",
                "post_url": None, "title_matched": None, "error": "未找到结果"}

    try:
        post_html = _fetch(best_url).decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return {"url": None, "candidates": [], "confidence": best_conf, "source": "media-db.example.com",
                "post_url": best_url, "title_matched": best_title,
                "error": f"抓取帖子失败: {e}"}
    cands = _rank_candidates(_extract_image_candidates(post_html), year)
    if not cands:
        return {"url": None, "candidates": [], "confidence": best_conf, "source": "media-db.example.com",
                "post_url": best_url, "title_matched": best_title, "error": "帖子无有效图片"}
    return {"url": cands[0], "candidates": cands, "confidence": best_conf,
            "source": "media-db.example.com", "post_url": best_url,
            "title_matched": best_title, "error": None}


def download_cover(media_row, candidates: list, cover_dir: str):
    """逐候选下载并校验封面，存入 cover_dir/{id}_{safe}.{ext}。返回 (path, wh) 或 (None, err)。"""
    last_err = "无候选"
    for u in _expand_candidates(candidates):
        try:
            data = _fetch(u, referer="https://media-db.example.com/")
        except Exception as e:  # noqa: BLE001
            last_err = f"下载失败: {e}"
            continue
        ok, w, h = _verify_image(data)
        if not ok:
            last_err = f"图片无效({w}x{h})"
            continue
        ext = os.path.splitext(urllib.parse.urlparse(u).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            ext = ".jpg"
        dest = os.path.join(cover_dir,
                            f"{media_row['id']}_{_safe_stem(media_row.get('title'))}{ext}")
        with open(dest, "wb") as f:
            f.write(data)
        return dest, (w, h)
    return None, last_err


def save_review(con, media_id: int, cand: dict) -> None:
    """把未自动采用的作品候选持久化进 cover_reviews（人工审定用）。

    - 有候选：保存候选图 + 帖子 + 置信度；
    - 无候选（未找到来源）：保存空候选 + 失败原因，供人工换关键词重搜。
    同一作品只保留一条 pending，重跑覆盖。
    """
    con.execute(
        "DELETE FROM cover_reviews WHERE media_id=? AND status='pending'", (media_id,))
    con.execute(
        "INSERT INTO cover_reviews(media_id, candidates, post_url, title_matched, "
        "confidence, error) VALUES(?,?,?,?,?,?)",
        (media_id, json.dumps(cand.get("candidates") or [], ensure_ascii=False),
         cand.get("post_url"), cand.get("title_matched"),
         cand.get("confidence", 0.0), cand.get("error")))


def run_online_cover_backfill(job) -> dict:
    """联网封面补全任务：对库中缺封面（磁盘无图）的作品搜索下载并写回。

    范围：job.meta['ids'] 指定的索引ID集合（由「A · 全量联网补全」解析；
    必须先确定范围才会启动该任务，避免误扫全库）。
    未能自动补齐的作品（低置信度 / 无源）会写入 cover_reviews 表，供人工审定。
    """
    cfg = cfg_mod.load()
    filter_cats = set(cfg["category_filter"])
    cover_dir = os.path.join(cfg_mod.BASE, CACHE_DIR)
    os.makedirs(cover_dir, exist_ok=True)
    raw_ids = (getattr(job, "meta", None) or {}).get("ids") or []
    id_set = {int(x) for x in raw_ids if str(x).strip().isdigit()}
    con = db.connect()
    db.init(con)
    try:
        where = f"category IN ({','.join('?'*len(filter_cats))})"
        rows = con.execute(
            f"SELECT id, title, file_path, poster_path, meta, edited_fields FROM media WHERE {where}",
            tuple(filter_cats)).fetchall()
        targets = []
        for r in rows:
            if id_set and r["id"] not in id_set:      # 严格限定在解析出的索引ID范围内
                continue
            if r["poster_path"] and os.path.exists(r["poster_path"]):
                continue
            try:   # 已用「视频预览帧」当封面的作品不再联网搜索
                if (json.loads(r["meta"] or "{}") or {}).get("cover_mode") == "video_frame":
                    continue
            except ValueError:
                pass
            if not os.path.exists(r["file_path"]):
                continue
            targets.append(r)
        total = len(targets)
        job.begin(total, current="联网搜索封面")
        filled = notfound = failed = skipped = done = 0
        detail = []
        for i, r in enumerate(targets):
            if job.stop.is_set():
                break
            edited = set(json.loads(r["edited_fields"] or "[]"))
            if "poster_path" in edited:
                skipped += 1
                done += 1
                job.tick(done, current=r["title"] or "")
                continue
            row = dict(r)
            cand = search_cover(row)
            if cand.get("error") or not cand.get("candidates"):
                notfound += 1
                save_review(con, r["id"], cand)
                detail.append({"id": r["id"], "title": r["title"],
                               "error": cand.get("error") or "无图", "ok": False})
                done += 1
                job.tick(done, current=r["title"] or "")
                continue
            if cand.get("confidence", 0.0) < MIN_CONF:
                notfound += 1
                save_review(con, r["id"], cand)
                detail.append({"id": r["id"], "title": r["title"],
                               "error": f"低置信度({cand['confidence']})", "ok": False})
                done += 1
                job.tick(done, current=r["title"] or "")
                continue
            path, wh = download_cover(row, cand["candidates"], cover_dir)
            if path:
                meta = json.loads(r["meta"] or "{}")
                meta["online_cover"] = {
                    "url": cand.get("url"), "post": cand.get("post_url"),
                    "source": cand.get("source"), "confidence": cand.get("confidence"),
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                con.execute(
                    "UPDATE media SET poster_path=?, meta=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (path, json.dumps(meta, ensure_ascii=False), r["id"]))
                filled += 1
                detail.append({"id": r["id"], "title": r["title"], "ok": True,
                               "cover": os.path.basename(path),
                               "confidence": cand.get("confidence")})
            else:
                failed += 1
                detail.append({"id": r["id"], "title": r["title"],
                               "error": wh, "ok": False})
            done += 1
            job.tick(done, current=r["title"] or "")
            if i % 10 == 0:
                con.commit()
        con.commit()
        return {"total": total, "filled": filled, "notfound": notfound,
                "failed": failed, "skipped": skipped, "canceled": bool(job.stop.is_set()),
                "scope_ids": len(id_set), "detail": detail}
    finally:
        con.close()
