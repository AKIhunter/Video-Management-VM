"""元数据提供方。

统一候选协议 resolve(media_row) -> dict（candidate）：

    {
      "synopsis": str | None,   # 简介（将按 ≤500 字截断）
      "tags": list[str],        # 标签（≤30 个、每个 ≤20 字，去重）
      "publish_date": str | None,  # 发布日期候选（YYYY-MM 或 YYYY-MM-DD，调用方规范化）
      "source": str | None,     # 来源标识，如 media-db.example.com / baidu
      "url": str | None,        # 候选来源链接（供人工审定核对）
      "confidence": float,      # 0~1，命中匹配置信度
      "error": str | None,      # 联网失败时的说明（非致命，调用方降级静默）
    }

关键设计——候选制：在线解析只产出"候选"，是否入库由管理员在白屏管理中
审定（admin 路由 metadata-refresh -> metadata-apply）。任何情况下不自动覆盖
已被人工编辑的字段（media.edited_fields）。
"""
import gzip
import html
import re
import urllib.request
from difflib import SequenceMatcher
from html.parser import HTMLParser

MAX_SYNOPSIS = 500   # 字数（教学/学习用途放宽：原为 100）
MAX_TAGS = 30        # 每个作品最多 tag 数（候选清洗上限；业务上限见 tagdict.TAG_LIMIT）
MAX_TAG_LEN = 20     # 单个 tag 最大字符数（原为 10）

# 发布日期候选正则：<time datetime="YYYY-MM..."> 或 sample 上传路径 uploads/YYYY/MM/
_TIME_DT = re.compile(r'<time[^>]+datetime=["\'](\d{4})-(\d{1,2})', re.I)
_UPLOAD_YM = re.compile(r'/uploads/(\d{4})/(\d{1,2})/', re.I)
_PUB_RE = re.compile(r'^(\d{4})-(\d{1,2})(?:-\d{1,2})?$')

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _normalize_publish_date(raw) -> str | None:
    """把候选发布日期规范化为 'YYYY-MM-01'（日份无意义，统一补 01）。

    接受 'YYYY-MM' / 'YYYY-MM-DD'；仅年份或格式不符则返回 None（调用方按 year 单独处理）。
    """
    if not raw:
        return None
    m = _PUB_RE.match(str(raw).strip())
    if not m:
        return None
    y, mo = m.group(1), int(m.group(2))
    if mo < 1 or mo > 12:
        return None
    return f"{y}-{mo:02d}-01"


def validate_candidate(cand: dict) -> dict:
    """约束校验 + 清洗（前后端一致规则的核心实现）。

    简介 ≤500 字；tag ≤30 个、每个 ≤20 字符、去重去空白。始终返回 dict。
    """
    cand = dict(cand or {})
    synops_ = (cand.get("synopsis") or "").strip()
    cand["synopsis"] = (synops_[:MAX_SYNOPSIS] + "…") if len(synops_) > MAX_SYNOPSIS else synops_

    tags, seen = [], set()
    for t in (cand.get("tags") or []):
        s = re.sub(r"\s+", " ", str(t)).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        tags.append(s[:MAX_TAG_LEN])
    cand["tags"] = tags[:MAX_TAGS]
    cand["publish_date"] = _normalize_publish_date(cand.get("publish_date"))
    cand["_validated"] = True
    return cand


def _strip_html_text(raw: str) -> str:
    """剥掉标签/脚本/样式，展开实体，压空白，粗略提纯正文文本。"""
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    # 去掉标题/来源等噪声链接类文本，仅保留可视文本
    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    p = Text()
    p.feed(raw)
    text = " ".join(p.parts)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class _HarvestParser(HTMLParser):
    """收集标签链接(rel=tag)与正文段落文本。"""

    def __init__(self):
        super().__init__()
        self._tag_buf = []
        self._tags = []
        self._paras = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and "tag" in a.get("rel", ""):
            self._cur = "tag"
            self._tag_buf = []
        elif tag == "a" and "category" in a.get("rel", ""):
            self._cur = "cat"
        elif tag in ("p", "div", "section") and self._paras and len(self._paras) <= 6:
            self._cur = "para"
            self._para_buf = []

    def handle_data(self, data):
        if self._cur == "tag":
            self._tag_buf.append(data)
        elif self._cur == "para":
            self._para_buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._cur == "tag":
            name = re.sub(r"\s+", " ", "".join(self._tag_buf)).strip()
            if name:
                self._tags.append(name)
            self._cur = None
        elif tag == "p" and self._cur == "para":
            txt = re.sub(r"\s+", " ", "".join(self._para_buf)).strip()
            if txt:
                self._paras.append(txt)
            self._cur = None


def _sentences(text: str, limit=MAX_SYNOPSIS) -> str:
    """取前若干完整句作为简介候选，保留标点。"""
    return text[:limit] if text else text


def _pick_best(results, keyword: str):
    """从 (title, *rest) 集合中选与搜索词关联度最高的项。

    返回 (keyboard_norm 匹配项)。可传入 None。
    """
    if not results:
        return None
    kw = normalize_keyword(keyword)
    if not kw:
        return results[0]
    best, best_r = None, 0.0
    for item in results:
        t = normalize_keyword(item[0])
        if not t:
            continue
        r = SequenceMatcher(None, t, kw).ratio()
        if r > best_r:
            best_r, best = r, item
    return best or results[0]


def normalize_keyword(s: str) -> str:
    """归一化搜索词：转小写、去空白（用于候选关联度比较）。"""
    return re.sub(r"\s+", "", str(s or "")).lower()


def synopsis_missing(media_row) -> bool:
    """判断某作品是否缺少简介（联网补全候选的目标集合）。"""
    return not (media_row.get("synopsis") or "").strip()


def build_keyword(media_row) -> str:
    """产出联网搜索词：优先日文原题，其次本地标题。"""
    return (media_row.get("title_jp") or media_row.get("title") or "").strip()


def extract_sample_results(html_text: str) -> list:
    """从 sample 搜索页解析 (标题, 链接)。按匹配度排序由调用方做。"""
    out = []
    # 常见 wordpress 结果：<h2 class="entry-title"><a href="..">title</a></h2>
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.S):
        url = m.group(1)
        title = _strip_html_text(m.group(2))
        if not title or not url.startswith("http"):
            continue
        if any(k in url for k in ("about", "category", "tag", "author", "page/")):
            continue
        out.append((title, url))
    # 去重保序
    seen, uniq = set(), []
    for t, u in out:
        if u in seen:
            continue
        seen.add(u)
        uniq.append((t, u))
    return uniq


def extract_sample_post(html_text: str) -> dict:
    """解析 sample 单篇：返回 {synopsis, tags, publish_date}。"""
    hp = _HarvestParser()
    hp.feed(html_text)
    plain = _strip_html_text(html_text)
    # 正文段优先取第一个足够长的段落；否则取去噪后的文本前缀
    synopsis = ""
    for para in hp._paras:
        if len(para) >= 20:
            synopsis = para
            break
    if not synopsis and plain:
        first = plain.split("首页")[-1].strip()
        synopsis = first[:MAX_SYNOPSIS] if first else ""
    # 发布日期候选：<time datetime="YYYY-MM"> 优先，否则上传路径 uploads/YYYY/MM/
    pub = None
    m_dt = _TIME_DT.search(html_text)
    if m_dt:
        pub = _normalize_publish_date(f"{m_dt.group(1)}-{m_dt.group(2)}")
    if not pub:
        m_up = _UPLOAD_YM.search(html_text)
        if m_up:
            pub = _normalize_publish_date(f"{m_up.group(1)}-{m_up.group(2)}")
    return {
        "synopsis": validate_candidate({"synopsis": synopsis})["synopsis"] or synopsis[:MAX_SYNOPSIS],
        "tags": hp._tags[:MAX_TAGS],
        "publish_date": pub,
    }


def extract_baidu_synopsis(html_text: str) -> list:
    """从百度结果页解析 (标题, 摘要, 链接)，按出现顺序。

    百度页面结构多变且为反爬做了混淆，尽量捕获 <div class="result ..."> 结果块
    中的标题与摘录文本；失败返回空列表（调用方降级到下一候选源）。
    """
    out = []
    blocks = re.split(r'<div class="result', html_text)
    for block in blocks[1:]:
        m_t = re.search(r"<h3[^>]*>.*?<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not m_t:
            continue
        url = m_t.group(1)
        title = _strip_html_text(m_t.group(2))
        if not title:
            continue
        abstract = _strip_html_text(block)
        out.append((title, abstract, url))
    seen, uniq = set(), []
    for t, a, u in out:
        if u in seen:
            continue
        seen.add(u)
        uniq.append((t, a, u))
    return uniq


def _fetch(url: str, timeout: float = 10.0) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "zh-CN,zh;q=0.9"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
    return data.decode("utf-8", "ignore")


class BaseResolver:
    name = "base"

    def resolve(self, media_row) -> dict:
        raise NotImplementedError


class LocalResolver(BaseResolver):
    """本地解析：仅用已入库信息，0 成本、无副作用。"""

    name = "local"

    def resolve(self, media_row):
        return {
            "title": media_row["title"],
            "title_jp": media_row["title_jp"] or media_row["title"],
            "studio": media_row["studio"],
            "publish_date": media_row["publish_date"],
            "rating": media_row["rating_norm"],
            "poster": media_row["poster_path"],
            "synopsis": media_row.get("synopsis"),
        }


class SampleResolver(BaseResolver):
    """联网候选：抓取 media-db.example.com 的搜索 + 单篇，产出简介/tag 候选。

    站点结构/可访问性随版本变化，所有网络与解析失败均降级返回空候选，
    不抛异常（error 字段说明原因），保证不阻塞本地索引。
    """

    name = "media-db.example.com"
    base = "https://media-db.example.com"

    def _search_url(self, keyword):
        import urllib.parse
        return f"{self.base}/wp/?s={urllib.parse.quote(keyword)}"

    def resolve(self, media_row) -> dict:
        keyword = build_keyword(media_row)
        try:
            html_text = _fetch(self._search_url(keyword))
        except Exception as e:  # noqa: BLE001
            return {"synopsis": None, "tags": [], "source": self.name,
                    "url": self._search_url(keyword), "confidence": 0,
                    "error": f"搜索失败: {e}"}
        results = extract_sample_results(html_text)
        if not results:
            return {"synopsis": None, "tags": [], "source": self.name,
                    "url": self._search_url(keyword), "confidence": 0,
                    "error": "未找到结果"}
        # 取关联度最高的结果（而非固定第一个），提高命中准确度
        best = _pick_best(results, keyword)
        best_title, best_url = best
        try:
            post_html = _fetch(best_url)
        except Exception as e:  # noqa: BLE001
            return {"synopsis": None, "tags": [], "source": self.name,
                    "url": best_url, "confidence": 0.3, "error": f"抓取帖子失败: {e}"}
        cand = extract_sample_post(post_html)
        cand.update({"source": self.name, "url": best_url, "title_matched": best_title,
                     "confidence": 0.8 if cand.get("tags") or cand.get("synopsis") else 0.3})
        return validate_candidate(cand)


class BaiduResolver(BaseResolver):
    """联网候选（百度）：优先采用，命中度最高摘要作为简介候选。

    Baidu 无稳定的 tag 结构，主要产出 synopsis 与核对链接；失败降级到 sample。
    """

    name = "baidu"
    base = "https://www.baidu.com/s"

    def _search_url(self, keyword):
        import urllib.parse
        return f"{self.base}?wd={urllib.parse.quote(keyword)}&ie=utf-8&rn=10"

    def resolve(self, media_row) -> dict:
        keyword = build_keyword(media_row)
        url = self._search_url(keyword)
        try:
            html_text = _fetch(url)
        except Exception as e:  # noqa: BLE001
            return {"synopsis": None, "tags": [], "source": self.name,
                    "url": url, "confidence": 0, "error": f"百度搜索失败: {e}"}
        results = extract_baidu_synopsis(html_text)
        if not results:
            return {"synopsis": None, "tags": [], "source": self.name,
                    "url": url, "confidence": 0, "error": "百度未解析到结果"}
        best = _pick_best(results, keyword)
        title, abstract, best_url = best
        sn = validate_candidate({"synopsis": abstract})["synopsis"] or None
        return validate_candidate({
            "synopsis": sn, "tags": [], "source": self.name,
            "url": best_url, "title_matched": title,
            "confidence": 0.6 if sn else 0.0,
            "error": None,
        })


class CascadeResolver(BaseResolver):
    """候选级联：本地 → 百度 → sample。

    - 本地已有简介，直接采用本地，不联网（优先本地信息源）。
    - 本地缺失时依序尝试百度、sample，返回首个产出非空候选的来源。
    任何来源失败都只是「没有可用简介」，不抛异常、不阻塞批量任务。
    """

    name = "cascade"

    def resolve(self, media_row) -> dict:
        local = LocalResolver().resolve(media_row)
        if local.get("synopsis"):
            return validate_candidate({**local, "source": "local", "url": None,
                                       "confidence": 1.0, "error": None})

        last = {"synopsis": None, "tags": [], "source": "none", "url": None,
                "confidence": 0, "error": "百度与 sample 均未取到有效信息"}
        for R in (BaiduResolver, SampleResolver):
            try:
                cand = R().resolve(media_row)
            except Exception as e:  # noqa: BLE001
                cand = {"synopsis": None, "tags": [], "source": R.name,
                        "url": None, "confidence": 0, "error": str(e)}
            last = cand
            if cand.get("synopsis") or cand.get("tags"):
                return cand
        return last


class OnlineResolver(BaseResolver):
    """聚合在线候选入口：级联（本地→百度→sample）。"""

    name = "online"

    def resolve(self, media_row) -> dict:
        return CascadeResolver().resolve(media_row)


def get_resolver(kind="local") -> BaseResolver:
    return {
        "local": LocalResolver(),
        "online": OnlineResolver(),
        "cascade": CascadeResolver(),
        "baidu": BaiduResolver(),
        "sample": SampleResolver(),
    }.get(kind, LocalResolver())