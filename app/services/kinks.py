"""题材词典与打标：把文本/候选标签映射为「按题材」的规范 tag。

设计：
  - KINK_ENTRIES 为有序「关键词正则 → 规范 tag」表（更具体/常见的题材在前）。
  - pick_tags(texts)     ：在文本中匹配词典，产出规范 tag（用于本机简评打标）。
  - filter_kink(tags)    ：对联网候选 tag 做归一化包含匹配，只保留词典命中的题材项。
  - TAG_LIMIT            ：每作品 tag 数上限（全局统一常量，admin/media/completion 共用）。
  - 两处均去重并按 limit（默认 TAG_LIMIT）截断。

注意：这里只提供「示例词条」，请按自己的片库题材替换 / 扩充 KINK_ENTRIES。
"""
import re

# 每作品 tag 数上限（教学/学习用途放宽：原为 3）
TAG_LIMIT = 10

# (关键词正则, 规范 tag)。顺序即优先级；关键词忽略大小写。
KINK_ENTRIES: list[tuple[str, str]] = [
    (r"action|战斗|动作|アクション", "动作"),
    (r"comedy|喜剧|コメディ", "喜剧"),
    (r"fantasy|奇幻|ファンタジー", "奇幻"),
    (r"sci-?fi|科幻|sf", "科幻"),
    (r"mystery|悬疑|ミステリー", "悬疑"),
    (r"daily|日常", "日常"),
    (r"sports?|竞技|スポーツ", "竞技"),
    (r"music|音乐|音楽", "音乐"),
    (r"school|校园|学園", "校园"),
    (r"romance|恋爱|ラブ", "恋爱"),
]

_COMPILED = [(re.compile(p, re.I), tag) for p, tag in KINK_ENTRIES]


def pick_tags(texts: list[str], limit: int = TAG_LIMIT) -> list[str]:
    """在 texts 中按词典顺序匹配，产出规范 tag（去重、截断 limit）。"""
    out: list[str] = []
    for text in texts or []:
        for rx, tag in _COMPILED:
            if tag in out:
                continue
            if rx.search(text or ""):
                out.append(tag)
            if len(out) >= limit:
                return out[:limit]
    return out[:limit]


def filter_kink(tags: list[str], limit: int = TAG_LIMIT) -> list[str]:
    """保留候选 tag 中命中词典的题材项（按候选顺序），并规范化、截断 limit。"""
    out: list[str] = []
    for t in tags or []:
        s = re.sub(r"\s+", " ", str(t or "")).strip()
        if not s:
            continue
        low = s.lower()
        for rx, tag in _COMPILED:
            if tag in out:
                continue
            if rx.search(low) or rx.search(s):
                out.append(tag)
                break
        if len(out) >= limit:
            return out[:limit]
    return out[:limit]
