"""按外部导出文件还原 media_tags 关联（**只重建 id↔id 关联，不写入/不保留标签值**）。

数据模型（三张表各司其职）：
  - media        ：作品单表（基础信息 + 索引ID）
  - tags         ：标签字典，单独记录 id → 标签值 的映射
  - media_tags   ：作品-标签**关联表**，只存 (media_id, tag_id)，不含标签值

本模块用途：若曾用「覆盖导入」清空过标签关联，可用早期导出的 JSON
（结构 `{tags:[{name, media_ids:[int]}, ...]}`）按**名称匹配**回库内已有标签 id，
仅重建 (media_id, tag_id) 关联行。

约定与约束：
  - 只匹配库内**已存在**的标签（名称完全相同）；**不新建标签、不写入任何标签名**；
    media_tags 只写 id，保持「关联表只存 id」的设计；
  - 每作品仍受 tagdict.TAG_LIMIT 上限保护，超出计入 capped；
  - 库中不存在的 media_id 计入 missing_media；
  - 名称仅在内存中用于匹配，不落库、不打印（调用方只输出计数）。
"""
import json

from .. import db
from .tagdict import TAG_LIMIT


def parse_export(data) -> list:
    """解析导出内容 → [(name, [media_id, ...]), ...]（兼容 JSON 文本/字节/dict/list）。"""
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
        try:
            data = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            data = raw.decode("gbk", "ignore")
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, list):
        data = {"tags": data}
    items = (data or {}).get("tags") or []
    out = []
    for it in items:
        if isinstance(it, str):
            out.append((it, []))
        elif isinstance(it, dict):
            out.append((it.get("name") or "", it.get("media_ids") or []))
    return out


def restore(con, data, dry_run: bool = True, limit: int = TAG_LIMIT) -> dict:
    """按导出内容还原关联。dry_run=True 时只统计不写库。返回计数字典。"""
    wanted = parse_export(data)

    name2id = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM tags").fetchall()}
    known_media = {r["id"] for r in con.execute("SELECT id FROM media").fetchall()}
    per_media = {r["media_id"]: r["c"] for r in con.execute(
        "SELECT media_id, COUNT(*) c FROM media_tags GROUP BY media_id").fetchall()}
    existing = {(r["media_id"], r["tag_id"]) for r in con.execute(
        "SELECT media_id, tag_id FROM media_tags").fetchall()}
    working = set(existing)   # 去重用的工作集（试运行时不影响 existing / links_after）

    stat = {
        "dry_run": dry_run,
        "file_tags": len(wanted),
        "matched_tags": 0,       # 文件名能在库内找到对应标签 id
        "unmatched_tags": 0,     # 库内无同名标签（不会新建）
        "links_added": 0,        # 新增关联数（dry_run 时为计划数）
        "skipped_existing": 0,   # 已存在，跳过
        "missing_media": 0,      # 库中无此索引ID
        "capped": 0,             # 超出每作品标签上限
        "touched_media": 0,
        "links_before": len(existing),
    }
    touched = set()
    for name, mids in wanted:
        tid = name2id.get(name)          # name 仅用于匹配，绝不落库/输出
        if tid is None:
            stat["unmatched_tags"] += 1
            continue
        stat["matched_tags"] += 1
        for mid in mids:
            m = int(mid) if str(mid).strip().isdigit() else None
            if m is None or m not in known_media:
                stat["missing_media"] += 1
                continue
            if (m, tid) in working:
                stat["skipped_existing"] += 1
                continue
            if per_media.get(m, 0) >= limit:
                stat["capped"] += 1
                continue
            if not dry_run:
                con.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES(?,?)",
                            (m, tid))
                existing.add((m, tid))
            working.add((m, tid))
            per_media[m] = per_media.get(m, 0) + 1
            touched.add(m)
            stat["links_added"] += 1
    if not dry_run:
        con.commit()
    stat["touched_media"] = len(touched)
    stat["links_after"] = len(existing)
    stat["total_tags"] = con.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    return stat


def restore_from_file(path: str, dry_run: bool = True, limit: int = TAG_LIMIT) -> dict:
    """读取导出文件并按当前配置的库还原关联（文件名/内容都不会被打印或另存）。"""
    with open(path, "rb") as f:
        raw = f.read()
    con = db.connect()
    db.init(con)
    try:
        return restore(con, raw, dry_run=dry_run, limit=limit)
    finally:
        con.close()
