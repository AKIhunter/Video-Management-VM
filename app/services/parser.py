"""文件名 / 海报 / 简评评分 解析。

三时代文件名规则：
  1) 2014-2015:  [字幕组][studio]标题[分辨率 编码].ext     （字幕为同目录 .ass 兄弟文件，无日期）
  2) 2015-2021:  [字幕组][studio]标题.ext                （无起始日期）
  3) 2022-2026:  [YYMMDD][studio]标题.chs.ext — POSTER 同名
"""
import os
import re

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmw", ".wmv", ".webm", ".flv", ".rmvb", ".mov"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
SUBTITLE_EXTS = {".ass", ".ssa", ".srt", ".sub"}

_BRACKET = re.compile(r"\[([^\[\]]*)\]")
_DATE6 = re.compile(r"^(\d{2})(\d{2})(\d{2})$")
_YEAR_DIR = re.compile(r"(20\d\d)年")
_MONTH_DIR = re.compile(r"(\d{1,2})月")


def derive_year_from_path(path: str) -> dict:
    """从文件夹路径段派生 年份/月份（用于文件名无日期时的回填）。

    形如 `…\2022年视频\2022年05月合集\…` → {year:2022, month:5}。
    返回含 year / month / publish_date(YYYY-MM-01，缺月为 None)。
    """
    if not path:
        return {"year": None, "month": None, "publish_date": None}
    segs = path.replace("\\", "/").split("/")
    year = month = None
    for seg in segs:
        my = _YEAR_DIR.search(seg)
        if my and year is None:
            year = int(my.group(1))
        mm = _MONTH_DIR.search(seg)
        if mm and month is None:
            month = int(mm.group(1))
    publish_date = None
    if year is not None and month is not None:
        publish_date = f"{year:04d}-{month:02d}-01"
    return {"year": year, "month": month, "publish_date": publish_date}


def normalize_title(s: str) -> str:
    """归一化标题用于模糊匹配：去空白/标点/全角转半角/小写，保留字母数字与中/日文。"""
    if not s:
        return ""
    s = s.strip()
    s = s.replace("\u3000", " ")
    s = re.sub(r"[《》「」『』【】()\[\]（）]|[-_~～・･.。，,、:：;；!！?？#'\"“”‘’/\\|<>*]", "", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _parse_date(yy, mm, dd):
    year = 2000 + int(yy)
    return f"{year:04d}-{int(mm):02d}-{int(dd):02d}", year


def parse_video_filename(name: str) -> dict:
    """解析视频文件名（去掉扩展名/后缀标志），返回 dict。

    keys: studio, title, title_jp, publish_date, year, subtitle, meta(brackets, extra注记)
    """
    if not name:
        return {}
    stem = name
    lower = stem.lower()
    subtitle = 1 if re.search(r"\.chs\.?(mp4|mkv|avi|webm)$", lower) or re.search(r"\.chs$", lower) else 0
    # 去掉 .chs 标记（形如 title.chs.mp4 -> title，再去掉容器扩展名）
    stem = re.sub(r"\.chs(?=\.(?:mp4|mkv|avi|webm))", "", stem, flags=re.I)
    stem = re.sub(r"\.(?:mp4|mkv|avi|wmv|webm|flv|rmvb|mov)$", "", stem, flags=re.I)

    brackets = [b for b in _BRACKET.findall(stem) if b.strip()]
    raw = _BRACKET.sub(" ", stem)
    raw = re.sub(r"\s+", " ", raw).strip()

    studio = None
    publish_date = None
    year = None
    title = raw

    if brackets and _DATE6.match(brackets[0].strip()):
        m = _DATE6.match(brackets[0].strip())
        publish_date, year = _parse_date(m.group(1), m.group(2), m.group(3))
        if len(brackets) > 1:
            studio = brackets[1].strip() or None

    elif brackets:
        # 首个非空括号视作制作组/字幕组
        studio = brackets[0].strip()

    return {
        "studio": studio,
        "title": title,
        "title_jp": None,
        "publish_date": publish_date,
        "year": year,
        "subtitle": subtitle,
        "meta": {"brackets": brackets},
    }


def find_poster(video_path: str) -> str | None:
    """在视频所在目录及其同级（如 XX月海报）目录中查找同名海报。

    查找顺序（尽力而为，命中即返回）：
      1) 直接同级同名（含含「海报」命名的兄弟/子目录）
      2) 向上一级（最多 3 层）子目录中查找同名
      3) 同级/上级目录中按标题关键字的模糊匹配
    """
    d = os.path.dirname(video_path)
    base = os.path.splitext(os.path.basename(video_path))[0]
    base = re.sub(r"\.chs$", "", base)
    n_base = normalize_title(base)

    def _scan_dir(cand_d):  # 在同目录及其中含「海报」的子目录里找
        hit = _search_same_basename(cand_d, base)
        if hit:
            return hit
        if os.path.isdir(cand_d):
            for sub in sorted(os.listdir(cand_d)):
                subp = os.path.join(cand_d, sub)
                if os.path.isdir(subp) and "海报" in sub:
                    hit = _search_same_basename(subp, base)
                    if hit:
                        return hit
        return None

    hit = _scan_dir(d)
    if hit:
        return hit

    # 向上爬升若干层，在祖先目录与其「海报」子目录中查找
    parent = os.path.dirname(d)
    climbed = 0
    while parent and parent != d and climbed < 3:
        hit = _scan_dir(parent)
        if hit:
            return hit
        d = parent
        parent = os.path.dirname(d)
        climbed += 1

    # 最后一搏：同级目录内按标题关键字的模糊匹配（限宽度，避免全盘低效）
    if n_base:
        hit = _search_fuzzy(d, n_base)
        if hit:
            return hit
        parent = os.path.dirname(d)
        if parent and parent != d:
            hit = _search_fuzzy(parent, n_base)
            if hit:
                return hit
    return None


def _search_same_basename(d: str, base: str):
    if not os.path.isdir(d):
        return None
    n_base = normalize_title(base)
    for fn in os.listdir(d):
        if os.path.splitext(fn)[1].lower() in IMAGE_EXTS:
            if normalize_title(os.path.splitext(fn)[0]) == n_base:
                return os.path.join(d, fn)
    return None


def _search_fuzzy(d: str, n_base: str):
    """在同目录的图片中，按标题关键字模糊命中（取与归一化标题匹配度最高的一张）。"""
    if not os.path.isdir(d) or not n_base:
        return None
    # 取关键字中的最长连续字母/数字/中日文段，避免括号噪声干扰匹配
    tokens = [t for t in re.split(r"[\s\-_~・~]{2,}|[《》「」『』【】]", n_base) if len(t) >= 2]
    if not tokens:
        tokens = [n_base]
    best, best_len = None, 0
    for fn in os.listdir(d):
        p = os.path.join(d, fn)
        if not os.path.isfile(p) or os.path.splitext(fn)[1].lower() not in IMAGE_EXTS:
            continue
        nfn = normalize_title(os.path.splitext(fn)[0])
        matched = sum(len(tok) for tok in tokens if tok in nfn)
        if matched > best_len:
            best_len, best = matched, p
    return best if best_len > 0 else None


# ---------------- 简评评分 ----------------

# 库内评分量纲：0~10（满分 10，最低 0）
RATING_MAX = 10.0
# 简评原文星级（0~5）→ 库内 0~10 的换算系数
_STAR_SCALE = 2.0


def _normalize_score_raw(text: str):
    """把 推荐度/评分/实用度 文案换算为原文星级 0~5（内部用，含「双档」处理）。"""
    if not text:
        return None
    text = text.strip()
    if "→" in text:
        text = text.split("→")[-1]
    score = None
    raw = text
    if "★" in text or "☆" in text:
        runs = re.findall(r"[★☆]+", text)
        if runs:
            run = runs[0]
            score = run.count("★") + 0.5 * run.count("☆")
    if score is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*星\s*(半)?", text)
        if m:
            score = float(m.group(1)) + (0.5 if m.group(2) else 0.0)
    if score is None:
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if m:
            score = float(m.group(1))
    # 双档：形如「0（纯主观）3.5（较客观）」取「较客观」的值
    if score is not None and score == 0 and r"客观" in raw:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*[（(][^)）]*?(?:客观|较客观)[^)）]*[)）]", raw)
        if m2:
            score = float(m2.group(1))
    return score


def normalize_score(text: str):
    """把 推荐度/评分/实用度 文案换算为**库内 0~10 分**（原文星级 0~5 × 2，封顶 10）。"""
    v = _normalize_score_raw(text)
    if v is None:
        return None
    return min(RATING_MAX, v * _STAR_SCALE)


def parse_jianping(text: str):
    """解析一期简评文本 -> [(title, score, raw_score)]（score 为库内 0~10）"""
    out = []
    tokens = re.split(r"(《[^》]+》)", text)
    cur = None
    chunk = ""
    for i, tok in enumerate(tokens):
        if i % 2 == 1:
            if cur is not None:
                out.append(_finalize(cur, chunk))
            cur = tok
            chunk = ""
        else:
            chunk += tok
    if cur is not None:
        out.append(_finalize(cur, chunk))
    return [o for o in out if o["score"] is not None]


def _finalize(title, tail):
    m = re.search(r"(?:推荐度|推薦度|评?评分|實用度|实用度)\s*[:：·]?\s*([^\n]+)", tail)
    score = None
    raw = ""
    if m:
        raw = m.group(1).strip()
        score = normalize_score(raw)
    return {"title": title.strip("《》"), "score": score, "raw_score": raw}