"""封面索引与匹配：解决历史作品封面展示率极低的问题。

磁盘上海报/封面/预告文件夹名称五花八门（X月海报/封面合集/COVER/Poster[海報]/作品海报…），
且视频深埋 `…【PSP】` 等子目录、封面在更上层、文件名常去掉 [字幕组][日期][PSP] 前缀。
旧 find_poster 只认「海报」目录且只能精确同名，系统性漏配。

本模块思路：一次性把全盘封面图片建成索引（核心词 → 候选 + 所属年/月），
匹配时先在「同一 年+月」的封面候选里按标题相似度打分，选最高且达阈值者；
命中不了的走 fallback（前端兜底图）。
"""
import difflib
import os
import re

from .. import config as cfg_mod
from .. import db
from ..core.media_helpers import edited
from .parser import (IMAGE_EXTS, VIDEO_EXTS, derive_year_from_path,
                     normalize_title)

# 命中即视为封面/预告素材目录的关键词（含新老格式各种命名 + 预览/壁纸等封面来源）
_COVER_KEYWORDS = ("海报", "封面", "cover", "poster", "预告", "预览", "壁纸", "pv",
                   "壁畫", "试写", "官网", "sample", "サンプル", "試写")
# 月份目录本身（如「2019年6月视频」）常平铺作品封面图，亦作封面来源
_MONTH_DIR = re.compile(r"\d{4}年\d{1,2}月")
# 文件名里会被剔除的标记词（分辨率/容器/字幕/设备等）
_TAG_WORDS = re.compile(
    r"(?i)\b(psp|hd|720p?|1080p?|2160p?|chs|cht|bdr?ip|dvdrip|raw|hdrip)\b|"
    r"[◆☆★◇●◎○]"
)
# 成对括号组（半角/全角/中文括号），整体剔除（内含字幕组/日期/编号等标签）
_BRACKETS = re.compile(r"[\u3010\u3011【】\[［\]］\(（)\[）]|"
                       r"[\uFF3B\uFF3D]")
_GROUP = re.compile(r"[\[【（(［][^\]】）)］]*[\]】）)］]")
_SUFFIX = re.compile(r"\s*(?:第?\s*[一二三四五六七八九十百\d]+\s*(?:话|話|卷|編|编|集)?\s*)$")
_EP_NO = re.compile(r"第\s*\d+\s*(话|話)")
# 公共系列/版本后缀（视频与封面两侧同去，避免不同作品因共享这些词而误配）
_SERIES_TAG = re.compile(
    r"(?i)\bthe\s*animation\b|\banimation\b|\banimeedition\b|\banime\s+edition\b"
    r"|\bOWS?\b|\bmovie\b|\bova\b"
)


def is_cover_dir(name: str) -> bool:
    """判断某个目录是否属于封面/预告素材目录。"""
    low = (name or "").lower()
    if any(k in low for k in _COVER_KEYWORDS):
        return True
    # 月份目录自带年份/月份（图片属于该作品的封面），也视为封面来源
    if _MONTH_DIR.search(name or ""):
        return True
    return False


def is_cover_file(name: str) -> bool:
    """文件名带 [预览]/[封面]/[海报] 等封面前缀，且不带分辨率等后缀的图片。"""
    low = (name or "").lower()
    if any(k in low for k in _COVER_KEYWORDS):
        stem = os.path.splitext(name)[0]
        if "[" in stem or "【" in stem or "〔" in stem:
            return True
    return False


def strip_tags(name: str) -> str:
    """去除封面/视频文件名里的标签噪声，得到用于匹配的归一化核心词。

    剔除：括号组（[字幕组]/[日期]/[PSP]/【】/［］/（））、分辨率/容器标记、
    装饰符号与数字编号后缀。
    """
    if not name:
        return ""
    s = name
    s = _GROUP.sub(" ", s)          # 去掉各种括号组及其内容
    s = s.replace("\uFF0D", "-").replace("－", "-")
    s = _TAG_WORDS.sub(" ", s)      # 去掉 PSP/720P/CHS 等
    s = _EP_NO.sub(lambda m: "第" + m.group(1), s)   # 集数序号归一化：第2話 → 第話（视频与封面一致，提升跨集匹配）
    s = _SERIES_TAG.sub(" ", s)     # 去掉 THE ANIMATION / Anime Edition / OVA 等公共版本词
    s = _SUFFIX.sub(" ", s)         # 去掉「第2话」「＃1」等编号后缀
    return normalize_title(s)


class CoverIndex:
    """已建立的全盘封面索引：core_title -> [(path, year, month), ...]。"""

    def __init__(self, entries):
        # entries: list of dict {path, core, year, month}
        self._by_core = {}
        self._all = entries
        for e in entries:
            self._by_core.setdefault(e["core"], []).append(e)

    @property
    def count(self) -> int:
        return len(self._all)

    def by_year_month(self, year, month):
        return [e for e in self._all if e["year"] == year and e["month"] == month]

    def all(self):
        return self._all


def build_cover_index(roots, stop=None) -> CoverIndex:
    """扫描 roots 下所有封面/预告类图片，建成索引。

    纳入口径有两种（满足其一即可）：
      - 所在目录名含封面关键词（海报/封面/预览/预告/壁纸/pv…）
      - 图片文件名本身带 [预览]/[封面]/[海报] 等封面前缀（封面常嵌在普通作品子目录内）
    """
    entries = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            if stop is not None and stop.is_set():
                return CoverIndex(entries)
            dir_cover = is_cover_dir(os.path.basename(dirpath))
            # 目录内同放视频与图片（无封面关键词）时，图片也是候选封面
            has_video = any(
                os.path.splitext(f)[1].lower() in VIDEO_EXTS for f in files)
            ym = derive_year_from_path(dirpath)
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in IMAGE_EXTS:
                    continue
                if not dir_cover and not has_video and not is_cover_file(fn):
                    continue
                p = os.path.abspath(os.path.join(dirpath, fn))
                if p in seen:
                    continue
                seen.add(p)
                entries.append({
                    "path": p,
                    "core": strip_tags(os.path.splitext(fn)[0]),
                    "year": ym.get("year"),
                    "month": ym.get("month"),
                })
    return CoverIndex(entries)


def _lcs_len(a: str, b: str):
    """最长公共子串长度（连续），用于捕捉「同系列标题」信号。"""
    la, lb = len(a), len(b)
    dp = [[0] * (lb + 1) for _ in range(2)]
    best = 0
    for i in range(1, la + 1):
        ai = a[i - 1]
        cur, prev = dp[1], dp[0]
        for j in range(1, lb + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
            else:
                cur[j] = 0
        dp[0], dp[1] = dp[1], dp[0]
    return best


def _ratio(a: str, b: str, series_weight=0.0):
    """标题相似度：SequenceMatcher 比例 + 前缀加成 + 可选系列(公共子串)加权。"""
    if not a or not b:
        return 0.0
    lcs = difflib.SequenceMatcher(None, a, b).ratio()
    # 前缀加成：命中长公共前缀通常更可靠
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    base = lcs + 0.12 * (i / max(len(a), len(b)))
    if series_weight > 0:
        ls = _lcs_len(a, b)
        base += series_weight * (ls / max(len(a), len(b)))
    return base


def _pick(cands, vcore, threshold, series_weight=0.0):
    """在候选里选相似度最高且 ≥threshold 者，返回 (path, score)。"""
    best, best_score = None, 0.0
    for e in cands:
        if not e["core"]:
            continue
        sc = _ratio(vcore, e["core"], series_weight)
        if sc > best_score:
            best_score, best = sc, e
    if best and best_score >= threshold:
        return best["path"], best_score
    return None, best_score


def resolve(video_path: str, index: CoverIndex, threshold=0.66, local_first=True):
    """为视频选封面。优先级：本地精确同名 → 同年月模糊 → 全局模糊(带系列加权)。"""
    if local_first:
        from .parser import find_poster
        p = find_poster(video_path)
        if p and os.path.exists(p):
            return p

    vcore = strip_tags(os.path.splitext(os.path.basename(video_path))[0])
    if not vcore:
        return None
    ym = derive_year_from_path(video_path)
    year, month = ym.get("year"), ym.get("month")

    # 同年月作用域优先：严格相似度（高精度，抑制误配）
    if year is not None and month is not None:
        cands = index.by_year_month(year, month)
        if cands:
            pth, sc = _pick(cands, vcore, threshold)
            if pth:
                return pth
    # 全局兜底：相似度 + 系列公共子串加权，捕捉「上/下巻·第X话·同系列不同集」
    pth, _sc = _pick(index.all(), vcore, threshold - 0.02, series_weight=0.38)
    return pth


def run_cover_backfill(job) -> dict:
    """一次性封面补全任务：为库中缺封面（或封面文件缺失）的作品匹配并写回。"""
    cfg = cfg_mod.load()
    filter_cats = set(cfg["category_filter"])
    con = db.connect()
    db.init(con)
    try:
        index = build_cover_index([os.path.abspath(r) for r in cfg["roots"]], stop=job.stop)
        if job.stop.is_set():
            return {"canceled": True, "indexed": index.count, "filled": 0, "total": 0}
        job.begin(0, current="建索引")

        where = f"category IN ({','.join('?'*len(filter_cats))})"
        rows = con.execute(
            f"SELECT id, file_path, poster_path, edited_fields FROM media WHERE {where}",
            tuple(filter_cats)).fetchall()
        targets = []
        for r in rows:
            pp = r["poster_path"]
            if not pp or not os.path.exists(pp):
                if not os.path.exists(r["file_path"]):
                    continue
                targets.append(r)
        total = len(targets)
        job.begin(total)
        filled = skipped = done = 0
        for i, r in enumerate(targets):
            if job.stop.is_set():
                break
            p = resolve(r["file_path"], index)
            if p and os.path.exists(p):
                # 封面属系统派生，不写 edited_fields；若管理员手动设过则尊重人工值
                edited = edited(r)
                if "poster_path" not in edited:
                    con.execute(
                        "UPDATE media SET poster_path=?, updated_at=datetime('now','localtime') WHERE id=?",
                        (p, r["id"]))
                    filled += 1
                else:
                    skipped += 1
            else:
                skipped += 1
            done += 1
            job.tick(done, total, current=f"{done}/{total}")
            if i % 50 == 0:
                con.commit()
        con.commit()
        return {"indexed": index.count, "total": total, "filled": filled,
                "skipped": skipped, "canceled": bool(job.stop.is_set())}
    finally:
        con.close()




# ---------------------------------------------------------------- 指定目录补全 / 视频帧封面标记
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
# 旧版「视频帧封面标记」的 meta.cover_mode 取值：界面入口已移除（由服务端抽帧替代），
# 仅作历史数据兼容 —— 抽帧成功后会清掉该标记，前端仍能渲染遗留的 video_frame 封面。
VIDEO_FRAME_MODE = "video_frame"


def build_dir_index(root: str, stop=None, limit: int = 50000) -> list:
    """扫描用户指定的本地目录，建立 [{path, core}] 索引（core = 去标签/去扩展名的文件名核心）。"""
    entries = []
    for dirpath, _dirs, files in os.walk(root):
        if stop is not None and stop.is_set():
            break
        for fn in files:
            if not fn.lower().endswith(IMG_EXTS):
                continue
            entries.append({"path": os.path.join(dirpath, fn),
                            "core": strip_tags(os.path.splitext(fn)[0])})
            if len(entries) >= limit:
                return entries
    return entries


def run_cover_dir_backfill(cfg, root: str, threshold: float = 0.62, stop=None,
                           ids=None) -> dict:
    """从「指定目录」取图片，与缺封面的作品按标题匹配并写回 poster_path。

    - 只写路径引用，**不复制、不移动、不重命名任何文件**；
    - 尊重人工编辑过的 poster_path（不覆盖）；
    - 已有可用封面的作品跳过（计入 skipped_has_cover）；
    - ids 指定时只处理这些索引ID。
    """
    from .parser import normalize_title
    id_set = {int(x) for x in (ids or []) if str(x).strip().isdigit()} or None
    con = db.connect()
    db.init(con)
    try:
        entries = build_dir_index(root, stop=stop)
        exact = {}
        for e in entries:
            if e["core"]:
                exact.setdefault(e["core"], e["path"])

        rows = con.execute(
            "SELECT id, title, title_jp, file_path, poster_path, edited_fields FROM media"
        ).fetchall()
        matched = no_match = skipped_edited = skipped_has_cover = 0
        used = set()
        for r in rows:
            if stop is not None and stop.is_set():
                break
            if id_set is not None and r["id"] not in id_set:
                continue
            pp = r["poster_path"]
            if pp and os.path.exists(pp):
                skipped_has_cover += 1
                continue
            if "poster_path" in edited(r):
                skipped_edited += 1
                continue
            cores = [strip_tags(r["title"] or ""), strip_tags(r["title_jp"] or "")]
            if not any(cores):
                cores = [strip_tags(os.path.splitext(os.path.basename(r["file_path"] or ""))[0])]
            hit = None
            for core in cores:
                if core and core in exact:
                    hit = exact[core]
                    break
            if not hit:
                for core in cores:
                    if not core:
                        continue
                    cands = [e for e in entries if e["path"] not in used] or entries
                    pth, _sc = _pick(cands, core, threshold, series_weight=0.08)
                    if pth:
                        hit = pth
                        break
            if hit:
                con.execute(
                    "UPDATE media SET poster_path=?, updated_at=datetime('now','localtime') "
                    "WHERE id=?", (hit, r["id"]))
                used.add(hit)
                matched += 1
            else:
                no_match += 1
        con.commit()
        return {"dir": root, "images": len(entries), "candidates": len(rows),
                "matched": matched, "no_match": no_match,
                "skipped_has_cover": skipped_has_cover, "skipped_edited": skipped_edited,
                "canceled": bool(stop is not None and stop.is_set())}
    finally:
        con.close()
