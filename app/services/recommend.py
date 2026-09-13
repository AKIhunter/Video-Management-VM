"""播放页「推荐轮播」——多因子加权打分与分页编排。

配合播放页底部的 2×6 轮播网格（每页 12 格，多页自动切换）：

    ┌ 候选池 ───────────────────────────────────────────┐
    │ 默认 = **同一分类**的作品（真人只推真人、视频只推视频）， │
    │ 排除当前播放条目；同分类无可选时兜底放开分类（scope 标记） │
    │ 分类是数据（categories / media.category），新增分类免改码 │
    └───────────────────────────────────────────────────┘
    ┌ 打分（线性加权，score 越高越靠前）──────────────────┐
    │ ① 标签重合度  shared / 当前作品标签数   ← 主因子    │
    │ ② 同制作组                            ← 系列一致性  │
    │ ③ 同分类（候选已按同分类硬过滤；此项仅在兜底放开时生效） │
    │ ④ 年代接近度（|Δyear| ≤ 10 线性衰减）                │
    │ ⑤ 评分质量（rating_norm 归一到 0~1）                 │
    │ ⑥ 观看状态（看完降权；想看/在看/未看加分）           │
    │ ⑦ 入库新鲜度（一年内线性衰减）                       │
    │ ⑧ 探索扰动（随机抖动，保证「换一批」有新意）         │
    └───────────────────────────────────────────────────┘
    ┌ 分页编排 ─────────────────────────────────────────┐
    │ 第 1 页：强相关（分数严格降序，最像当前作品）        │
    │ 第 2 页：同档洗牌（相关性相近但顺序打散）            │
    │ 第 3 页：探索池（继续降档 + 随机，跨出同类目）        │
    │ 每页施加「同制作组上限」约束，避免一页被同一系列包场  │
    └───────────────────────────────────────────────────┘

铁律遵守：**打分与排序**只读取 id 列、聚合计数、数值字段（年份/评分/时长），
绝不因此读取标签文本；标签相关性以 COUNT(*) 参与运算。
唯一例外是**卡片展示**：按需求把「与当前作品共享的标签 id」映射成标签值随结果返回
（``shared_tag_names``，见 ``_shared_tag_names``），仅用于前端展示，不参与打分。
"""
from __future__ import annotations

import random

# —— 因子权重（调大即增强该维度的影响力）——
W_TAG = 4.0          # ① 标签重合度：占绝对主导，内容相关性最强信号
W_STUDIO = 1.6       # ② 同制作组：系列/画风一致性
W_CATEGORY = 0.5     # ③ 同分类：弱加成，保证不跑偏但不过度同质
W_YEAR = 0.9         # ④ 年代接近：同期作品审美相近
W_RATING = 1.2       # ⑤ 评分质量：好片优先
W_STATUS = 0.4       # ⑥ 观看状态
W_FRESH = 0.35       # ⑦ 入库新鲜度
JITTER = 0.9         # ⑧ 随机扰动上限（探索性，覆盖评级差异≤1.2分以下的小差距）

MAX_SAME_STUDIO = 3      # 同一页内同制作组作品数上限（硬约束，仅在无人可选时突破）
STUDIO_PENALTY = 1.5     # 同制作组多样性惩罚：本页每多一格，等效分扣 1.5
POOL_FACTOR = 4          # 参与编排的候选倍数（per_page × pages × 4，长尾不参与以提速）
YEAR_SPAN = 10           # 年代接近度的衰减跨度（年）
FRESH_DAYS = 365         # 新鲜度的线性衰减跨度（天）
SHARED_TAG_LIMIT = 6     # 卡片上最多展示几个「共享标签」名称（防卡片过长）
SAME_CATEGORY_ONLY = True  # True：只在**同一分类**内推荐（真人只推真人、视频只推视频，可扩展到任意新分类）
                           # False：候选不限分类（此时仅靠 W_CATEGORY 弱加成偏好同类）

# 观看状态 → 分数修正系数（相对于 W_STATUS）
_STATUS_FACTOR = {
    "想看": 0.9,
    "在看": 0.6,
    "看完": -2.0,   # 已看完的重复推荐价值低，明显降权
    "未看": 1.0,
}


def _profile(con, mid: int) -> dict | None:
    """当前作品画像：仅取分类/制作组/年份/评分与标签 id 集合（不读标签文本）。"""
    row = con.execute(
        "SELECT category, studio, year, "
        "COALESCE(rating_avg, rating_norm) AS rating_norm FROM media WHERE id=?", (mid,)
    ).fetchone()
    if row is None:
        return None
    tag_ids = [r["tag_id"] for r in con.execute(
        "SELECT tag_id FROM media_tags WHERE media_id=?", (mid,)).fetchall()]
    return {
        "category": row["category"],
        "studio": row["studio"],
        "year": row["year"],
        "rating": row["rating_norm"],
        "tag_ids": tag_ids,
    }


def _candidates(con, mid: int, uid: int, tag_ids: list, category: str = "") -> list:
    """一次性拉取候选集并顺带算出「共享标签数」（避免 N+1 查询）。

    ``category`` 非空时只取**同一分类**的作品（真人只推真人、视频只推视频）；
    留空表示不限分类。新增分类无需改代码 —— 分类是数据（``categories`` 字典 + ``media.category``）。
    """
    cat_sql = " AND m.category = ?" if category else ""
    cat_params = [category] if category else []
    if tag_ids:
        ph = ",".join("?" * len(tag_ids))
        sql = f"""
            SELECT m.id, m.title, m.poster_path, m.category, m.studio, m.year,
                   m.publish_date,
                   COALESCE(m.rating_avg, m.rating_norm) AS rating_norm,
                   m.duration_sec, m.meta,
                   COALESCE(ws.status, '未看') AS status,
                   COALESCE(sh.c, 0)           AS shared,
                   sh.tids                     AS tids,
                   (julianday('now') - julianday(m.created_at)) AS age_days
            FROM media m
            LEFT JOIN watch_state ws
                   ON ws.media_id = m.id AND ws.user_id = ?
            LEFT JOIN (
                   SELECT mt.media_id AS mid, COUNT(*) AS c,
                          GROUP_CONCAT(mt.tag_id) AS tids
                   FROM media_tags mt
                   WHERE mt.tag_id IN ({ph})
                   GROUP BY mt.media_id
            ) sh ON sh.mid = m.id
            WHERE m.id != ?{cat_sql}
        """
        params = [uid, *tag_ids, mid, *cat_params]
    else:
        sql = f"""
            SELECT m.id, m.title, m.poster_path, m.category, m.studio, m.year,
                   m.publish_date,
                   COALESCE(m.rating_avg, m.rating_norm) AS rating_norm,
                   m.duration_sec, m.meta,
                   COALESCE(ws.status, '未看') AS status,
                   0 AS shared,
                   NULL AS tids,
                   (julianday('now') - julianday(m.created_at)) AS age_days
            FROM media m
            LEFT JOIN watch_state ws
                   ON ws.media_id = m.id AND ws.user_id = ?
            WHERE m.id != ?{cat_sql}
        """
        params = [uid, mid, *cat_params]
    return con.execute(sql, params).fetchall()


def _score(cand, prof: dict, rnd: random.Random,
           rating_lo: float = 0.0, rating_hi: float = 1.0) -> tuple[float, list]:
    """对单个候选打分，并返回用于前端徽章的理由标记。

    ``rating_lo/rating_hi`` 为全库评分区间，用于把评分归一到 0~1
    （库内评分量纲并非固定 0~1，必须按实际区间归一才有效）。
    """
    score = 0.0
    marks = []

    # ① 标签重合度（0~1）：主因子
    n_tags = len(prof["tag_ids"])
    if n_tags:
        overlap = min(1.0, (cand["shared"] or 0) / n_tags)
        if overlap > 0:
            score += W_TAG * overlap
            marks.append("tag")

    # ② 同制作组
    if prof["studio"] and cand["studio"] and cand["studio"] == prof["studio"]:
        score += W_STUDIO
        marks.append("studio")

    # ③ 同分类
    if prof["category"] and cand["category"] == prof["category"]:
        score += W_CATEGORY
        marks.append("category")

    # ④ 年代接近度
    if prof["year"] and cand["year"]:
        gap = abs(int(cand["year"]) - int(prof["year"]))
        if gap <= YEAR_SPAN:
            score += W_YEAR * (1.0 - gap / YEAR_SPAN)
            if gap <= 3:
                marks.append("era")

    # ⑤ 评分质量（按全库区间归一到 0~1）
    if cand["rating_norm"] is not None:
        span = float(rating_hi) - float(rating_lo)
        norm = 0.5 if span <= 0 else (float(cand["rating_norm"]) - float(rating_lo)) / span
        score += W_RATING * max(0.0, min(1.0, norm))

    # ⑥ 观看状态
    score += W_STATUS * _STATUS_FACTOR.get(cand["status"] or "未看", 1.0)

    # ⑦ 入库新鲜度（一年内线性加成）
    age = cand["age_days"]
    if age is not None and age >= 0:
        score += W_FRESH * max(0.0, 1.0 - float(age) / FRESH_DAYS)

    # ⑧ 探索扰动
    score += JITTER * rnd.random()
    return score, marks


def _arrange(scored: list, per_page: int, pages: int,
             cap: int = MAX_SAME_STUDIO, penalty: float = STUDIO_PENALTY) -> list:
    """每页贪心编排：相关性优先，同时用「多样性惩罚」打散同一制作组。

    逐格挑选当前等效分最高的候选：``等效分 = 原始分 − penalty × 该制作组本页已用格数``；
    当某制作组已达页内上限时追加巨额惩罚，只有在别无选择时才允许突破。
    这样既保证每页格数固定（永远不空位），又让同一系列不会包场。
    """
    total = min(len(scored), per_page * pages * POOL_FACTOR)
    pool = scored[:total]                       # 只对高分段做编排，长尾不参与
    taken = [False] * len(pool)
    out = []
    for _ in range(pages):
        page, in_page, used = [], set(), {}
        while len(page) < per_page:
            best_i, best_val = -1, None
            for i, (s, c, _m) in enumerate(pool):
                if taken[i] or i in in_page:
                    continue
                st = c["studio"] or ""
                pen = penalty * used.get(st, 0)
                if st and used.get(st, 0) >= cap:
                    pen += 100.0                # 硬上限：重罚，仅在无人可选时才突破
                v = s - pen
                if best_val is None or v > best_val:
                    best_i, best_val = i, v
            if best_i < 0:
                break
            page.append((best_i, *pool[best_i]))
            in_page.add(best_i)
            st = pool[best_i][1]["studio"] or ""
            used[st] = used.get(st, 0) + 1
        if not page:
            break
        for i, _s, _c, _m in page:
            taken[i] = True
        out.append(page)
    return out


def _shared_tag_names(cand, tag_names: dict) -> list:
    """把「与当前作品共享的标签 id」映射成标签值（按 id 升序，去重并截断）。

    仅用当前作品自身的标签 id 去查 names（调用方一次查好传入），cand 里只带 id 列表。
    """
    raw = None
    try:
        raw = cand["tids"]
    except (KeyError, IndexError, TypeError):
        raw = None
    if not raw:
        return []
    ids = []
    for x in str(raw).split(","):
        x = x.strip()
        if x.isdigit():
            tid = int(x)
            if tid not in ids:
                ids.append(tid)
    ids.sort()
    out = []
    for tid in ids:
        nm = tag_names.get(tid)
        if nm and nm not in out:
            out.append(nm)
    return out[:SHARED_TAG_LIMIT]


def _serialize(cand, score: float, marks: list, prof: dict, tag_names: dict | None = None) -> dict:
    meta = {}
    if cand["meta"]:
        try:
            import json
            meta = json.loads(cand["meta"]) or {}
        except (ValueError, TypeError):
            meta = {}
    return {
        "id": cand["id"],
        "title": cand["title"],
        "poster_path": cand["poster_path"],
        "cover_mode": meta.get("cover_mode"),   # video_frame → 前端走视频帧兜底
        "category": cand["category"],
        "studio": cand["studio"],
        "year": cand["year"],
        "publish_date": cand["publish_date"],   # 卡片上展示发布时间
        "rating_norm": cand["rating_norm"],
        "duration_sec": cand["duration_sec"],
        "status": cand["status"],
        # 排序/徽章用（score 为打分结果，shared_tags 为计数）
        "score": round(score, 3),
        "shared_tags": int(cand["shared"] or 0),
        # 卡片徽章：具体「共享标签」的名称（由标签 id 映射而来，替代原来的「N 个相同标签」模糊文案）
        "shared_tag_names": _shared_tag_names(cand, tag_names or {}),
        "reason": marks,
    }


def recommend(con, mid: int, uid: int = 1, per_page: int = 12, pages: int = 3,
              seed: int | None = None) -> dict:
    """产出可直接喂给轮播组件的分页推荐。

    返回 ``{"source_id", "per_page", "page_count", "pages": [[item, ...], ...]}``。
    第 1 页强相关，第 2 页同档洗牌，第 3 页起为探索池。
    """
    prof = _profile(con, mid)
    if prof is None:
        return {"source_id": mid, "per_page": per_page, "page_count": 0, "pages": []}

    per_page = max(1, min(int(per_page or 12), 24))
    pages = max(1, min(int(pages or 3), 6))
    rnd = random.Random(seed) if seed is not None else random.Random()

    rows = _candidates(con, mid, uid, prof["tag_ids"],
                       category=(prof["category"] or "") if SAME_CATEGORY_ONLY else "")
    scope = "same_category" if (SAME_CATEGORY_ONLY and prof["category"]) else "all"
    if not rows and scope == "same_category":
        # 同分类下没有其他作品（新分类 / 分类写错）→ 兜底放开分类，避免轮播整块空白
        rows = _candidates(con, mid, uid, prof["tag_ids"])
        scope = "fallback_all"
    # 标签 id → 标签值（仅当前作品自己的标签，用于卡片上展示「共享标签」）
    tag_names = {}
    if prof["tag_ids"]:
        ph = ",".join("?" * len(prof["tag_ids"]))
        for r in con.execute(
                f"SELECT id, name FROM tags WHERE id IN ({ph})", prof["tag_ids"]).fetchall():
            tag_names[r["id"]] = r["name"]
    rr = con.execute(
        "SELECT MIN(COALESCE(rating_avg, rating_norm)) lo, "
        "       MAX(COALESCE(rating_avg, rating_norm)) hi FROM media "
        "WHERE COALESCE(rating_avg, rating_norm) IS NOT NULL").fetchone()
    rating_lo = rr["lo"] if rr and rr["lo"] is not None else 0.0
    rating_hi = rr["hi"] if rr and rr["hi"] is not None else 1.0
    scored = []
    for c in rows:
        s, marks = _score(c, prof, rnd, rating_lo, rating_hi)
        scored.append((s, c, marks))
    scored.sort(key=lambda x: x[0], reverse=True)

    out_pages = [
        [_serialize(c, s, m, prof, tag_names) for _i, s, c, m in page]
        for page in _arrange(scored, per_page, pages)
    ]
    pool = per_page * pages

    return {
        "source_id": mid,
        "per_page": per_page,
        "page_count": len(out_pages),
        "pages": out_pages,
        # 候选范围：same_category（同分类，默认）/ fallback_all（同分类无人时兜底放开）/ all（未限定分类）
        "scope": scope,
        "profile": {
            "has_tags": bool(prof["tag_ids"]),
            "tag_count": len(prof["tag_ids"]),
            "studio": prof["studio"],
            "year": prof["year"],
            "category": prof["category"],
            "candidate_total": len(scored),
            "pool_used": min(pool, len(scored)),
        },
    }
