"""白屏管理（后台管理）：媒体运维编辑 / tag / 联网补全审定 / 扫描 / 一键重启。

权限：v1 免登录，默认 admin（见 authz）。未来接入登录层后由 require_admin 自动守卫。
"""
import base64
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import config as cfg_mod
from .. import db
from ..services import jobs
from ..services import scanner
from ..authz import require_admin
from ..services.backfill import run_metadata_backfill
from ..services.completion import run_completion
from ..services.covers import run_cover_backfill, run_cover_dir_backfill
from ..db import get_db
from ..services.frames import run_frame_backfill
from ..services.kinks import TAG_LIMIT
from ..services.metadata_provider import MAX_TAG_LEN, MAX_TAGS, OnlineResolver, validate_candidate
from ..services.online_covers import (_fetch, download_cover, save_review, search_cover,
                             run_online_cover_backfill)
from ..services.tagging import run_tag_backfill
from ..services.ratings import RATING_MAX
from ..core.media_helpers import media_or_404

router = APIRouter(prefix="/api/admin", tags=["admin"])

RESTART_CONFIRM = "__RESTART__"

# 管理页可编辑的媒体字段 → 落库列名（写 edited_fields 用同一 key）
EDITABLE = {
    "title": "title",
    "title_jp": "title_jp",
    "studio": "studio",
    "publish_date": "publish_date",
    "year": "year",
    "subtitle": "subtitle",
    "file_path": "file_path",
    "poster_path": "poster_path",
    "rating_norm": "rating_norm",
    "rating_raw": "rating_raw",
    "rating_source": "rating_source",
    "synopsis": "synopsis",
}


def _scan_worker(job) -> dict:
    """扫描 = **只产出待导入清单**（dry_run，不写库）；人工点「导入」才真正入库。

    清单进 ``scan_stage`` 暂存区（进程内存），由 `GET /scan/staged` 展示、`POST /scan/import` 落库。
    """
    from ..services import scan_stage

    cfg = cfg_mod.load()
    scope = job.meta.get("scope", "full")
    path = job.meta.get("path")

    def progress(done, total, current=None):
        job.tick(done, total, current=current)

    stage: list = []
    result = scanner.do_scan(cfg, dry_run=True, scope=scope, path=path,
                             stop=job.stop, progress=progress,
                             category_override=job.meta.get("category"), stage=stage)
    n = scan_stage.set_stage(stage, {"scope": scope, "path": path,
                                     "category": job.meta.get("category") or ""})
    result["staged"] = n
    result["note"] = "已生成待导入清单，请到下方确认后点「导入到索引」"
    return result


def _scan_import_worker(job) -> dict:
    """导入暂存清单（kind 仍为 scan，复用状态/取消/通知通道；meta.phase=import）。"""
    from ..services import scan_stage

    category = job.meta.get("category") or ""
    items = scan_stage.items()

    def progress(done, total, current=None):
        job.tick(done, total, current=current)

    result = scanner.import_staged(items, category, progress=progress, stop=job.stop)
    if not result.get("canceled"):
        scan_stage.clear()
    result["note"] = ("导入完成，暂存清单已清空" if not result.get("canceled")
                      else "导入已取消，暂存清单保留")
    return result


def _completion_worker(job) -> dict:
    return run_completion(job, source=job.meta.get("source", "online"),
                          mid=job.meta.get("mid"), ids=job.meta.get("ids"))


def _metadata_worker(job) -> dict:
    return run_metadata_backfill(job)


def _cover_worker(job) -> dict:
    return run_cover_backfill(job)


def _cover_online_worker(job) -> dict:
    return run_online_cover_backfill(job)


def _tags_worker(job) -> dict:
    return run_tag_backfill(job)


def _frame_worker(job) -> dict:
    return run_frame_backfill(job, ids=job.meta.get("ids"))


# ---- 消息通知 ----
_WORKER_LABELS = {
    "scan": "扫描", "completion": "联网补全", "metadata": "元数据回填",
    "cover": "封面补全", "cover-online": "联网封面", "tags": "本机打标",
    "frame": "服务端抽帧",
}


def _push_notification(ntype: str, title: str, body: str = None) -> None:
    """写一条通知到 notifications 表。全 try 包裹，失败绝不影响主任务。"""
    try:
        con = db.connect()
        db.init(con)
        con.execute(
            "INSERT INTO notifications(type,title,body) VALUES(?,?,?)",
            (ntype, title, body))
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001
        pass


def _fmt_summary(label: str, result) -> str:
    """把任务结果 dict 折算成人类可读的一句话日志。"""
    if not isinstance(result, dict):
        return f"{label}：{result}"
    parts = [label]
    keys = ("total", "filled", "tagged", "added", "updated", "removed", "scanned",
            "date_filled", "year_filled", "tags_online", "tags_local",
            "indexed", "notfound", "skipped", "failed")
    for k in keys:
        v = result.get(k)
        if v:
            parts.append(f"{k}={v}")
    if result.get("canceled"):
        parts.append("已取消")
    if result.get("failed_reason"):
        parts.append(f"示例:{result['failed_reason']}")
    return " · ".join(parts)


def _notify_wrap(label: str, fn):
    """包装 worker：完成/取消/失败时各推一条通知。"""
    def run(job):
        try:
            result = fn(job)
            canceled = bool(isinstance(result, dict) and result.get("canceled"))
            _push_notification(
                "task_canceled" if canceled else "task_done",
                f"{label}{'已取消' if canceled else '完成'}",
                _fmt_summary(label, result))
            return result
        except Exception as e:  # noqa: BLE001
            _push_notification("task_error", f"{label}失败", f"{e}")
            raise
    return run




def _edited_fields(con, mid: int) -> set:
    m = media_or_404(con, mid)
    try:
        return set(json.loads(m["edited_fields"] or "[]"))
    except ValueError:
        return set()


def set_media_tags(con, mid: int, tags: list) -> None:
    """整组替换某作品的 tag（tags 视为唯一、按名称去重）。"""
    con.execute("DELETE FROM media_tags WHERE media_id=?", (mid,))
    for name in tags:
        name = (name or "").strip()
        if not name:
            continue
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES(?,?)", (mid, tid))


def media_tags(con, mid: int) -> list:
    rows = con.execute(
        "SELECT t.name FROM tags t JOIN media_tags mt ON mt.tag_id=t.id "
        "WHERE mt.media_id=? ORDER BY t.name", (mid,)).fetchall()
    return [r["name"] for r in rows]


def _media_row_dict(con, mid: int) -> dict:
    m = media_or_404(con, mid)
    out = {k: m[k] for k in m.keys()}
    try:
        out["edited_fields"] = json.loads(out.get("edited_fields") or "[]")
    except ValueError:
        out["edited_fields"] = []
    return out


# ---------------------------------------------------------------- 运维编辑
@router.post("/media/{mid}/edit")
def edit_media(mid: int, body: dict, _u=Depends(require_admin), con=Depends(get_db)):
    """编辑标题/简介/tag/文件地址等。tags 为整组替换。写过的字段记入 edited_fields。"""
    m = media_or_404(con, mid)
    edited = set(json.loads(m["edited_fields"] or "[]"))
    fields = {k: v for k, v in (body or {}).items() if k in EDITABLE and v is not None}

    if "synopsis" in fields:
        fields["synopsis"] = validate_candidate({"synopsis": fields["synopsis"]})["synopsis"]
    if "rating_norm" in fields:
        try:
            rn = float(fields["rating_norm"])
        except (TypeError, ValueError):
            raise HTTPException(400, "评分必须是数字")
        fields["rating_norm"] = max(0.0, min(float(RATING_MAX), rn))

    upd = []
    for key in fields:
        upd.append(f"{EDITABLE[key]}=?")
    if upd:
        con.execute(
            f"UPDATE media SET {', '.join(upd)}, "
            f"edited_fields=?, updated_at=datetime('now','localtime') "
            f"WHERE id=?",
            [fields[k] for k in fields] + [json.dumps(sorted(edited | set(fields)), ensure_ascii=False), mid],
        )
        edited |= set(fields)

    if "tags" in body and body["tags"] is not None:
        tags = validate_candidate({"tags": body["tags"]})["tags"][:TAG_LIMIT]  # 每作品最多 TAG_LIMIT 个
        set_media_tags(con, mid, tags)
        edited.add("tags")
        con.execute(
            "UPDATE media SET edited_fields=?, updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(sorted(edited), ensure_ascii=False), mid),
        )
    con.commit()
    return {"ok": True, "media": _media_row_dict(con, mid), "tags": media_tags(con, mid)}


# ---------------------------------------------------------------- 用户管理（管理中心 · 增删改查）
def _default_user_id() -> int:
    """当前登录体系解析用的默认账号 id（不可删除 / 不可降级，否则会把自己锁在门外）。"""
    return int(cfg_mod.load().get("default_user_id") or 1)


@router.get("/users")
def list_users(_u=Depends(require_admin), con=Depends(get_db)):
    """账号列表（含等级 / 成长值 / 签到概况）。"""
    from ..services import levels
    rows = con.execute(
        "SELECT id, name, role, points, checkin_days, checkin_streak, last_checkin, created_at "
        "FROM users ORDER BY id").fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["level"] = levels.level_of(d["points"] or 0)
        d["is_default"] = d["id"] == _default_user_id()
        items.append(d)
    return {"items": items, "default_user_id": _default_user_id()}


@router.post("/users")
def create_user(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """新增账号（name 必填 ≤40 字；role ∈ admin/user）。"""
    body = body or {}
    name = (body.get("name") or "").strip()[:40]
    role = (body.get("role") or "user").strip().lower()
    if not name:
        raise HTTPException(400, "账号名不能为空")
    if role not in ("admin", "user"):
        raise HTTPException(400, "role 只能是 admin / user")
    if con.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
        raise HTTPException(400, "账号名已存在")
    con.execute("INSERT INTO users(name, role) VALUES(?,?)", (name, role))
    con.commit()
    row = con.execute("SELECT id, name, role FROM users WHERE name=?", (name,)).fetchone()
    return {"ok": True, "user": dict(row)}


@router.put("/users/{uid}")
def update_user(uid: int, body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """修改账号（改名 / 改角色）。默认登录账号不可降级为普通用户（防止把管理员锁在门外）。"""
    row = con.execute("SELECT id, name, role FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(404, "账号不存在")
    body = body or {}
    name = (body.get("name") or "").strip()[:40] if "name" in body else row["name"]
    role = (body.get("role") or row["role"]).strip().lower()
    if not name:
        raise HTTPException(400, "账号名不能为空")
    if role not in ("admin", "user"):
        raise HTTPException(400, "role 只能是 admin / user")
    if uid == _default_user_id() and role != "admin":
        raise HTTPException(400, "默认登录账号不能降级为普通用户（会失去管理权限）")
    if name != row["name"] and con.execute(
            "SELECT 1 FROM users WHERE name=? AND id!=?", (name, uid)).fetchone():
        raise HTTPException(400, "账号名已存在")
    con.execute("UPDATE users SET name=?, role=? WHERE id=?", (name, role, uid))
    con.commit()
    return {"ok": True, "user": {"id": uid, "name": name, "role": role}}


@router.delete("/users/{uid}")
def delete_user(uid: int, _u=Depends(require_admin), con=Depends(get_db)):
    """删除账号：同时清掉该账号的观看状态 / 评分 / 收藏 / 备注（媒体与标签不受影响）。"""
    if uid == _default_user_id():
        raise HTTPException(400, "默认登录账号不能删除")
    row = con.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(404, "账号不存在")
    con.execute("DELETE FROM watch_state WHERE user_id=?", (uid,))
    con.execute("DELETE FROM users WHERE id=?", (uid,))
    con.commit()
    return {"ok": True, "deleted": uid, "name": row["name"]}


@router.get("/usergroups")
def list_usergroups(_u=Depends(require_admin)):
    return {"items": [], "note": "规划中：后续支持组内权限与批量授权"}


# ---------------------------------------------------------------- 标签管理（增删改查）
def _normalize_tag_name(name) -> str:
    import re
    return re.sub(r"\s+", " ", str(name or "")).strip()[:MAX_TAG_LEN]


def _mark_tagsedited(con, mid: int) -> None:
    """把 tags 记入 edited_fields，避免后续联网补全覆盖人工标签。"""
    row = con.execute("SELECT edited_fields FROM media WHERE id=?", (mid,)).fetchone()
    if row is None:
        return
    try:
        edited = set(json.loads(row["edited_fields"] or "[]"))
    except ValueError:
        edited = set()
    if "tags" not in edited:
        edited.add("tags")
        con.execute(
            "UPDATE media SET edited_fields=?, updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(sorted(edited), ensure_ascii=False), mid))


def bulk_tag_media(con, mids: list, tags: list, mode: str = "append") -> dict:
    """批量打标签（供 /media/bulk 的 action=tag 复用）。

    mode=append（默认）：追加标签，保留原有关联（受 TAG_LIMIT 保护，超出计入 capped）；
    mode=replace：用给定标签整组替换原有关联；
    mode=remove：仅移除给定标签，保留其余关联。
    仅在内容确实变化时写库（幂等）；写过的作品记入 edited_fields 含 tags。
    """
    added = removed = capped = 0
    affected = 0
    for mid in mids:
        cur = media_tags(con, mid)
        if mode == "replace":
            target = list(tags[:TAG_LIMIT])
            capped += max(0, len(tags) - TAG_LIMIT)
        elif mode == "remove":
            target = [n for n in cur if n not in tags]
        else:  # append
            target = list(cur)
            for nm in tags:
                if nm in target:
                    continue
                if len(target) >= TAG_LIMIT:
                    capped += 1
                    continue
                target.append(nm)
        if target == cur:
            continue                       # 无变化：跳过（幂等，避免无谓写入）
        set_media_tags(con, mid, target)
        added += len(set(target) - set(cur))
        removed += len(set(cur) - set(target))
        _mark_tagsedited(con, mid)
        affected += 1
    return {"affected": affected, "added_links": added,
            "removed_links": removed, "capped": capped}


@router.get("/tags")
def list_all_tags(_u=Depends(require_admin), con=Depends(get_db)):
    rows = con.execute(
        "SELECT t.id, t.name, COUNT(mt.media_id) c FROM tags t "
        "LEFT JOIN media_tags mt ON mt.tag_id=t.id "
        "GROUP BY t.id ORDER BY t.name").fetchall()
    return {"items": [dict(r) for r in rows]}


# ---- 标签导出 / 导入（纯文本：每行 `<id>,<标签值>`；覆盖 or 增量）----
def _parse_tag_lines(text: str):
    """解析标签文本：每行 `<id>,<标签值>`（英文逗号分隔、换行分隔）。

    容错规则：
      - 空行 / 以 # 开头的注释行 → 跳过；
      - 首列为纯数字 → (id, 标签值)；
      - 首列为表头（id / tag_id / 标签id / 索引id）→ 跳过；
      - 无逗号或首列非数字 → 整行视为标签值（id=None，自动分配）。
    标签值按行原样返回，由调用方做长度/空白规范化。
    """
    records = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        first, sep, rest = line.partition(",")
        first = first.strip()
        name = rest.strip()
        if sep and first.isdigit():
            if name:
                records.append((int(first), name))
            continue
        if sep and first.lower() in ("id", "tag_id", "标签id", "索引id"):
            continue  # 表头
        records.append((None, line))
    return records


@router.get("/tags/export")
def export_tags(_u=Depends(require_admin), con=Depends(get_db)):
    """导出标签字典：每行 `<id>,<标签值>`，英文逗号分隔、换行分隔。

    仅输出 tags 表的 id 与 name 两列（不含作品内容、不含关联关系）。
    **带 UTF-8 BOM**（\\ufeff 前缀）：让 Excel / 记事本 / WPS 在中文 Windows 上
    也能正确识别为 UTF-8（否则会被当成 ANSI/GBK 打开而显示乱码，用户另存后
    再导入就会把标签值写成乱码）。
    """
    lines = [f"{r['id']},{r['name']}" for r in con.execute(
        "SELECT id, name FROM tags ORDER BY id").fetchall()]
    body = "\ufeff" + "\n".join(lines) + ("\n" if lines else "")
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tags_export.csv"'})


def _decode_text_bytes(raw: bytes):
    """按 utf-8-sig → utf-8 → gbk → big5 依次探测解码，返回 (文本, 编码名)。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "big5"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None, None


def _backup_tags(con) -> dict:
    """覆盖导入前自动备份标签与关联（服务端落盘，供人工回滚）。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(cfg_mod.BASE, "backups")
    os.makedirs(out_dir, exist_ok=True)
    tag_f = os.path.join(out_dir, f"tags_backup_{ts}.csv")
    link_f = os.path.join(out_dir, f"media_tags_backup_{ts}.csv")
    with open(tag_f, "w", encoding="utf-8-sig", newline="") as f:
        for r in con.execute("SELECT id, name FROM tags ORDER BY id").fetchall():
            f.write(f"{r['id']},{r['name']}\n")
    with open(link_f, "w", encoding="utf-8-sig", newline="") as f:
        for r in con.execute("SELECT media_id, tag_id FROM media_tags").fetchall():
            f.write(f"{r['media_id']},{r['tag_id']}\n")
    return {"dir": "backups", "files": [os.path.basename(tag_f), os.path.basename(link_f)]}


@router.post("/tags/import")
def import_tags(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """导入标签（自行解析文本，每行 `<id>,<标签值>`）。

    入参二选一：
      - data_b64：文件**原始字节**的 base64（前端推荐）→ 服务端按
        utf-8-sig → utf-8 → gbk → big5 探测解码，避免 ANSI/GBK 文件被误当 UTF-8；
      - data：已解码的文本（兼容/程序化调用）。
    任一方式下，若文本含替换字符 U+FFFD（解码失败的痕迹）→ 直接拒绝，防止写入乱码。

    mode=merge（默认，增量）：只新增缺失标签，保留现有标签与全部作品关联；
      文件中的 id 若未被占用则沿用，否则自动分配新 id（计入 remapped）。
    mode=overwrite（覆盖）：需 confirm_text="__OVERWRITE__"；先自动备份
      tags / media_tags 到 backups/，再清空重建（文件不含关联，关联会被解除）。
    """
    body = body or {}
    mode = (body.get("mode") or "merge").strip().lower()
    if mode not in ("merge", "overwrite"):
        raise HTTPException(400, "mode 只能是 merge 或 overwrite")

    data_b64 = body.get("data_b64")
    encoding = "text"
    if data_b64:
        try:
            raw = base64.b64decode(data_b64)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"base64 解码失败：{e}") from e
        text, encoding = _decode_text_bytes(raw)
        if text is None:
            raise HTTPException(400, "无法识别文件编码（支持 UTF-8 / UTF-8-BOM / GBK / Big5），请另存为 UTF-8 后重试")
    else:
        text = body.get("data")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "缺少导入数据（data_b64 或 data）")

    if "\ufffd" in text:
        raise HTTPException(
            400, "文件编码不正确（含无法解码的替换字符 U+FFFD）。"
                 "请用本系统导出的文件，或在记事本/Excel 中「另存为 UTF-8」后再导入")

    # 解析（先解析校验，再动数据）：兼容旧版 JSON 导出 {tags:[...]} / [name,...]
    records = None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        records = []
        items = parsed if isinstance(parsed, list) else (parsed.get("tags") or [])
        for it in items:
            if isinstance(it, str):
                records.append((None, it))
            elif isinstance(it, dict):
                records.append((None, it.get("name") or ""))
    if records is None:
        records = _parse_tag_lines(text)
    if not records:
        raise HTTPException(400, "文件中未解析到任何标签行（应为每行 `id,标签值`）")

    backup = None
    snapshot = []
    if mode == "overwrite":
        if (body.get("confirm_text") or "").strip() != "__OVERWRITE__":
            raise HTTPException(400, "覆盖导入需 confirm_text=__OVERWRITE__ 确认")
        # 覆盖前按名称快照现有「作品-标签关联」，替换字典后按名称重新绑定
        # （名称仅在内存中用于匹配，不落库、不输出、不新增）
        snapshot = [(r["media_id"], r["name"]) for r in con.execute(
            "SELECT mt.media_id, t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id").fetchall()]
        backup = _backup_tags(con)
        con.execute("DELETE FROM media_tags")
        con.execute("DELETE FROM tags")

    added_tags = skipped = remapped = kept_ids = 0
    for tid, raw_name in records:
        name = _normalize_tag_name(raw_name)
        if not name:
            skipped += 1
            continue
        if con.execute("SELECT 1 FROM tags WHERE name=?", (name,)).fetchone():
            skipped += 1  # 已存在（或文件内重复行）
            continue
        if tid is not None and not con.execute("SELECT 1 FROM tags WHERE id=?", (tid,)).fetchone():
            con.execute("INSERT INTO tags(id, name) VALUES(?,?)", (tid, name))
            kept_ids += 1
        else:
            if tid is not None:
                remapped += 1
            con.execute("INSERT INTO tags(name) VALUES(?)", (name,))
        added_tags += 1

    # 覆盖导入后：按标签名把原有作品-标签关联重新绑定到新字典的 id 上，
    # 使「只更新标签字典」不再导致关联脱绑（只写 id，不写名称）。
    rebound = lost = 0
    if mode == "overwrite" and snapshot:
        name2id = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM tags").fetchall()}
        per_media = {r["media_id"]: r["c"] for r in con.execute(
            "SELECT media_id, COUNT(*) c FROM media_tags GROUP BY media_id").fetchall()}
        for mid, name in snapshot:
            tid = name2id.get(name)
            if tid is None:          # 新字典里已无同名标签，无法找回
                lost += 1
                continue
            if per_media.get(mid, 0) >= TAG_LIMIT:
                lost += 1
                continue
            cur = con.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES(?,?)",
                              (mid, tid))
            if cur.rowcount:
                per_media[mid] = per_media.get(mid, 0) + 1
                rebound += 1
    con.commit()

    total_tags = con.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    total_links = con.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0]
    return {"ok": True, "mode": mode, "format": "csv-line", "encoding": encoding,
            "added_tags": added_tags, "skipped": skipped,
            "remapped": remapped, "kept_ids": kept_ids,
            "rebound_links": rebound, "lost_links": lost,
            "total_tags": total_tags, "total_links": total_links,
            "backup": backup}


@router.post("/tags/recover")
def recover_tags(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """标签误覆盖后的恢复辅助（**只回报计数，不返回任何标签值**）。

    actions:
      - drop_garbled：删除名称含 U+FFFD（解码失败留下的替换符）的乱码标签及其关联；
      - restore_meta：按 media.meta.auto_tags（本机简评打标留痕）重建标签与关联。
    dry_run 默认 true（仅统计不落库）；确认后传 dry_run=false 执行。
    """
    body = body or {}
    actions = set(body.get("actions") or ["drop_garbled", "restore_meta"])
    dry = bool(body.get("dry_run", True))

    garbled_ids = [r["id"] for r in con.execute(
        "SELECT id FROM tags WHERE instr(name, char(65533))>0").fetchall()]
    garbled_links = 0
    for t in garbled_ids:
        garbled_links += con.execute(
            "SELECT COUNT(*) FROM media_tags WHERE tag_id=?", (t,)).fetchone()[0]

    meta_rows = con.execute(
        "SELECT id, meta FROM media WHERE instr(coalesce(meta,''), 'auto_tags')>0").fetchall()
    recoverable = 0
    for r in meta_rows:
        try:
            tags = ((json.loads(r["meta"] or "{}").get("auto_tags")) or {}).get("tags") or []
        except ValueError:
            tags = []
        if tags:
            recoverable += 1

    report = {
        "dry_run": dry,
        "garbled_tags": len(garbled_ids),
        "garbled_links": garbled_links,
        "recoverable_media": recoverable,
    }

    if not dry:
        if "drop_garbled" in actions and garbled_ids:
            ph = ",".join("?" * len(garbled_ids))
            con.execute(f"DELETE FROM media_tags WHERE tag_id IN ({ph})", garbled_ids)
            con.execute(f"DELETE FROM tags WHERE id IN ({ph})", garbled_ids)
        if "restore_meta" in actions:
            restored = links = 0
            for r in meta_rows:
                try:
                    tags = ((json.loads(r["meta"] or "{}").get("auto_tags")) or {}).get("tags") or []
                except ValueError:
                    continue
                if not tags:
                    continue
                cur = con.execute("SELECT COUNT(*) FROM media_tags WHERE media_id=?",
                                  (r["id"],)).fetchone()[0]
                if cur >= TAG_LIMIT:
                    continue
                for name in tags:
                    if cur >= TAG_LIMIT:
                        break
                    nm = _normalize_tag_name(name)
                    if not nm:
                        continue
                    con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (nm,))
                    tid = con.execute("SELECT id FROM tags WHERE name=?", (nm,)).fetchone()["id"]
                    if con.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=?",
                                   (r["id"], tid)).fetchone():
                        continue
                    con.execute("INSERT INTO media_tags(media_id, tag_id) VALUES(?,?)",
                                (r["id"], tid))
                    cur += 1
                    links += 1
                restored += 1
            report["restored_media"] = restored
            report["restored_links"] = links
        con.commit()

    report["total_tags"] = con.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    report["total_links"] = con.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0]
    return {"ok": True, **report}


@router.post("/tags")
def create_tag(body: dict, _u=Depends(require_admin), con=Depends(get_db)):
    name = _normalize_tag_name((body or {}).get("name"))
    if not name:
        raise HTTPException(400, "标签名不能为空")
    con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
    con.commit()
    row = con.execute("SELECT id, name FROM tags WHERE name=?", (name,)).fetchone()
    return {"ok": True, "tag": dict(row)}


@router.put("/tags/{tid}")
def rename_tag(tid: int, body: dict, _u=Depends(require_admin), con=Depends(get_db)):
    name = _normalize_tag_name((body or {}).get("name"))
    if not name:
        raise HTTPException(400, "标签名不能为空")
    if not con.execute("SELECT 1 FROM tags WHERE id=?", (tid,)).fetchone():
        raise HTTPException(404, "标签不存在")
    dup = con.execute("SELECT id FROM tags WHERE name=? AND id!=?", (name, tid)).fetchone()
    if dup:
        raise HTTPException(400, "标签名已存在")
    con.execute("UPDATE tags SET name=? WHERE id=?", (name, tid))
    con.commit()
    return {"ok": True}


@router.delete("/tags/{tid}")
def delete_tag(tid: int, confirm_name: str = "",
               _u=Depends(require_admin), con=Depends(get_db)):
    """删除标签并解除关联。

    防误触（双层）：前端需在弹窗中输入标签名解锁；后端校验 confirm_name
    必须与该标签当前名称完全一致，否则拒绝（400）。
    """
    row = con.execute("SELECT name FROM tags WHERE id=?", (tid,)).fetchone()
    if row is None:
        raise HTTPException(404, "标签不存在")
    if (confirm_name or "").strip() != (row["name"] or "").strip():
        raise HTTPException(400, "防误触校验失败：confirm_name 需与标签名完全一致")
    links = con.execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=?", (tid,)).fetchone()[0]
    con.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
    con.execute("DELETE FROM tags WHERE id=?", (tid,))
    con.commit()
    return {"ok": True, "deleted": row["name"], "removed_links": links}


# ---------------------------------------------------------------- 作品搜索（筛选）与 ID 下载
def _admin_filters(q="", has_cover=None, has_synopsis=None, has_tags=None, tag="", year=None,
                   category=""):
    """后台作品筛选条件（作品编辑 / 下载索引ID / 作品管理 共用同一套语义）。

    category 留空 = **不限分类**（导出/查询全部已索引作品）；指定时只看该分类。
    """
    where, params = [], []
    if category:
        where.append("m.category = ?")
        params.append(category)
    if q:
        like = f"%{q}%"
        where.append("(m.title LIKE ? ESCAPE '\\' OR m.title_jp LIKE ? ESCAPE '\\' "
                     "OR m.studio LIKE ? OR CAST(m.id AS TEXT)=?)")
        params += [like, like, like, q.strip()]
    if has_cover == 1:
        where.append("(m.poster_path IS NOT NULL AND m.poster_path != '')")
    elif has_cover == 0:
        where.append("(m.poster_path IS NULL OR m.poster_path = '')")
    if has_synopsis == 1:
        where.append("(m.synopsis IS NOT NULL AND m.synopsis != '')")
    elif has_synopsis == 0:
        where.append("(m.synopsis IS NULL OR m.synopsis = '')")
    if has_tags == 1:
        where.append("EXISTS (SELECT 1 FROM media_tags mt WHERE mt.media_id=m.id)")
    elif has_tags == 0:
        where.append("NOT EXISTS (SELECT 1 FROM media_tags mt WHERE mt.media_id=m.id)")
    if tag:
        where.append("EXISTS (SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id "
                     "WHERE mt.media_id=m.id AND (m.title LIKE ? ESCAPE '\\' OR t.name LIKE ? ESCAPE '\\'))")
        like = f"%{tag}%"
        params += [like, like]
    if year:
        where.append("m.year = ?")
        params.append(int(year))
    return where, params


@router.get("/media/search")
def search_media(
    q: str = "", has_cover: int = None, has_synopsis: int = None,
    has_tags: int = None, tag: str = "", year: int = None, category: str = "",
    limit: int = Query(3000, le=10000), _u=Depends(require_admin), con=Depends(get_db),
):
    """带基本信息筛选的作品清单：封面/简介/标签是否为空、标签关键字、年份、分类。"""
    where, params = _admin_filters(q, has_cover, has_synopsis, has_tags, tag, year, category)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = con.execute(
        f"SELECT m.id, m.title, m.title_jp, m.year, m.studio, "
        f"CASE WHEN m.poster_path IS NOT NULL AND m.poster_path != '' THEN 1 ELSE 0 END has_cover, "
        f"CASE WHEN m.synopsis IS NOT NULL AND m.synopsis != '' THEN 1 ELSE 0 END has_synopsis "
        f"FROM media m {where_sql} ORDER BY m.id LIMIT ?",
        [*params, limit]).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/media/ids.txt")
def download_media_ids(
    q: str = "", has_cover: int = None, has_synopsis: int = None,
    has_tags: int = None, tag: str = "", year: int = None, category: str = "",
    _u=Depends(require_admin), con=Depends(get_db),
):
    """导出**当前查询结果**的作品索引ID（每行一个 id）；不传筛选条件即导出全部作品。"""
    where, params = _admin_filters(q, has_cover, has_synopsis, has_tags, tag, year, category)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = con.execute(
        f"SELECT m.id FROM media m {where_sql} ORDER BY m.id",
        [*params]).fetchall()
    content = "".join(f"{r['id']}\n" for r in rows)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=media_ids.txt"},
    )


# ---------------------------------------------------------------- 分类字典（与扫描/页面筛选/作品管理共用）
@router.get("/categories")
def list_categories(_u=Depends(require_admin), con=Depends(get_db)):
    """分类清单 + 各分类下作品数。"""
    db.sync_categories(con)
    con.commit()
    rows = con.execute(
        "SELECT c.id, c.name, COUNT(m.id) c FROM categories c "
        "LEFT JOIN media m ON m.category = c.name GROUP BY c.id ORDER BY c.name").fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/categories")
def create_category(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    name = (body or {}).get("name") or ""
    name = name.strip()[:40]
    if not name:
        raise HTTPException(400, "分类名不能为空")
    if con.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone():
        raise HTTPException(400, "分类已存在")
    con.execute("INSERT INTO categories(name) VALUES(?)", (name,))
    con.commit()
    return {"ok": True, "name": name}


@router.put("/categories/{cid}")
def rename_category(cid: int, body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """改分类名：同步把该分类下所有作品的 category 一起改名（不触碰文件）。"""
    row = con.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()
    if row is None:
        raise HTTPException(404, "分类不存在")
    old = row["name"]
    name = ((body or {}).get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "分类名不能为空")
    if name == old:
        return {"ok": True, "name": name, "moved": 0}
    if con.execute("SELECT 1 FROM categories WHERE name=? AND id!=?", (name, cid)).fetchone():
        raise HTTPException(400, "分类名已存在")
    con.execute("UPDATE categories SET name=? WHERE id=?", (name, cid))
    cur = con.execute("UPDATE media SET category=?, updated_at=datetime('now','localtime') "
                      "WHERE category=?", (name, old))
    con.commit()
    return {"ok": True, "name": name, "moved": cur.rowcount}


@router.delete("/categories/{cid}")
def delete_category(cid: int, _u=Depends(require_admin), con=Depends(get_db)):
    """删分类：仅允许删除**空分类**（分类下还有作品时请先移动或删除作品）。"""
    row = con.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()
    if row is None:
        raise HTTPException(404, "分类不存在")
    n = con.execute("SELECT COUNT(*) FROM media WHERE category=?", (row["name"],)).fetchone()[0]
    if n:
        raise HTTPException(400, f"该分类下还有 {n} 部作品，请先移动或删除后再删除分类")
    con.execute("DELETE FROM categories WHERE id=?", (cid,))
    con.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 作品管理（按分类：列表 / 移动 / 删索引）
@router.get("/media/list")
def list_media_admin(
    category: str = "", q: str = "", page: int = 1, size: int = Query(50, le=200),
    _u=Depends(require_admin), con=Depends(get_db),
):
    """作品管理列表：按分类 + 模糊查询（索引ID / 作品名 / 标签）。"""
    where, params = [], []
    if category:
        where.append("m.category = ?")
        params.append(category)
    if q:
        like = f"%{q}%"
        where.append(
            "(CAST(m.id AS TEXT) = ? OR m.title LIKE ? ESCAPE '\\' OR m.title_jp LIKE ? ESCAPE '\\' "
            "OR m.studio LIKE ? ESCAPE '\\' "
            "OR EXISTS (SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id "
            "           WHERE mt.media_id=m.id AND t.name LIKE ? ESCAPE '\\'))")
        params += [q.strip(), like, like, like, like]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = con.execute(f"SELECT COUNT(*) FROM media m {where_sql}", params).fetchone()[0]
    rows = con.execute(
        f"SELECT m.id, m.category, m.title, m.year, m.publish_date, m.studio, "
        f"CASE WHEN (m.poster_path IS NOT NULL AND m.poster_path != '') "
        f"          OR instr(coalesce(m.meta,''), 'video_frame') > 0 THEN 1 ELSE 0 END has_cover "
        f"FROM media m {where_sql} ORDER BY m.id LIMIT ? OFFSET ?",
        [*params, size, (page - 1) * size]).fetchall()
    return {"total": total, "page": page, "size": size, "items": [dict(r) for r in rows]}


# ---- 重复作品检测（同名 / 高度相似标题，供人工去重）----
DUP_SIMILARITY_DEFAULT = 0.86   # 标题相似度阈值（与 scanner 里评分匹配的 0.85 同量纲）
DUP_SCAN_LIMIT = 5000           # 单次最多参与比对的条数（防超大库把请求拖死）
DUP_GRAM_POST_LIMIT = 120       # 单个作品最多比对的候选组数（防高频 gram 拖慢）
DUP_MAX_COMPARE = 300000        # 单次比对次数上限（时间保护）
DUP_IGNORE_SETTINGS_KEY = "dup_ignore_keys"   # 「本组忽略」名单（settings，JSON 数组）


def _dup_norm(title: str) -> str:
    from ..services.parser import normalize_title
    return normalize_title(title or "")


def _load_dup_ignore(con) -> set:
    import json as _json
    raw = db.get_setting(con, DUP_IGNORE_SETTINGS_KEY) or "[]"
    try:
        data = _json.loads(raw)
    except ValueError:
        return set()
    return {str(x) for x in data if isinstance(x, str) and x}


def _dup_candidate_rows(con, category):
    where, params = ("WHERE m.category = ?", [category]) if category else ("", [])
    return con.execute(
        f"SELECT m.id, m.category, m.title, m.title_jp, m.year, "
        f"CASE WHEN (m.poster_path IS NOT NULL AND m.poster_path != '') "
        f"          OR instr(coalesce(m.meta,''), 'video_frame') > 0 THEN 1 ELSE 0 END has_cover "
        f"FROM media m {where} ORDER BY m.id LIMIT ?",
        [*params, DUP_SCAN_LIMIT]).fetchall()


def _dup_groups(con, category="", threshold=None):
    """在指定分类内找出标题（归一化后）高度相似的作品分组。

    算法：标题归一化（``parser.normalize_title``）→ 用 3-gram 倒排为每个「组代表」建索引
    （只比对至少共享一个 gram 的候选）→ 与**组代表**的相似度 ≥ 阈值才并入该组，
    否则自成一新组。采用「代表式聚类」而非单链聚类，避免 A~B、B~C 把一堆不同作品串成一大组。
    分块 + 比对次数上限保证数千条量级仍是秒级；仅读取 id/标题/年份，不读标签文本与路径。
    """
    from difflib import SequenceMatcher
    from ..services.parser import normalize_title

    thr = float(threshold if threshold is not None else DUP_SIMILARITY_DEFAULT)
    rows = _dup_candidate_rows(con, category)
    n = len(rows)
    items = []
    for r in rows:
        norm = normalize_title(r["title"] or "") or normalize_title(r["title_jp"] or "")
        items.append({"row": r, "norm": norm})

    groups = []        # [{"rep": idx, "members": [idx, ...]}]
    rep_grams = {}     # 3-gram -> [组下标, ...]
    compares = 0

    for i, it in enumerate(items):
        s = it["norm"]
        if not s:
            continue
        keys = {s} if len(s) < 4 else {s[j:j + 3] for j in range(len(s) - 2)}

        cand = []
        for g in keys:
            for gi in rep_grams.get(g, ()):
                if gi not in cand:
                    cand.append(gi)
            if len(cand) >= DUP_GRAM_POST_LIMIT:
                break

        target = None
        for gi in cand[:DUP_GRAM_POST_LIMIT]:
            if compares >= DUP_MAX_COMPARE:
                break
            rep = items[groups[gi]["rep"]]["norm"]
            if not rep:
                continue
            compares += 1
            if SequenceMatcher(None, s, rep).ratio() >= thr:
                target = gi
                break

        if target is None:
            groups.append({"rep": i, "members": [i]})
            gi = len(groups) - 1
            for g in keys:
                rep_grams.setdefault(g, []).append(gi)
        else:
            groups[target]["members"].append(i)

    out = []
    for grp in groups:
        if len(grp["members"]) < 2:
            continue
        grp["members"].sort(key=lambda i: items[i]["row"]["id"])
        out.append([items[i] for i in grp["members"]])
    # 已忽略的组不再返回（「本组忽略」按组内成员的归一化标题匹配，删掉代表项也不影响）
    ignored = _load_dup_ignore(con)
    ignored_groups = sum(1 for g in out if any(it["norm"] in ignored for it in g))
    out = [g for g in out if not any(it["norm"] in ignored for it in g)]
    # 组内条数多的排前面；同数量按最小 id
    out.sort(key=lambda g: (-len(g), g[0]["row"]["id"]))

    def _pub(it):
        r = it["row"]
        return {"id": r["id"], "category": r["category"], "title": r["title"],
                "title_jp": r["title_jp"], "year": r["year"], "has_cover": r["has_cover"]}

    result = [{"key": g[0]["norm"], "items": [_pub(it) for it in g]} for g in out]
    return {"category": category, "threshold": thr, "scanned": n,
            "truncated": n >= DUP_SCAN_LIMIT, "ignored_groups": ignored_groups,
            "ignored_total": len(ignored), "groups": result}


@router.get("/media/duplicates")
def list_duplicate_media(category: str = "", threshold: float = None,
                         _u=Depends(require_admin), con=Depends(get_db)):
    """按分类列出「标题高度相似」的作品分组，供人工去重。

    仅返回 id / 标题 / 年份 / 分类 / 有无封面，不涉及标签文本与文件路径。
    选定保留项后，用 ``POST /media/bulk`` 的 ``action=delete`` 删除其余重复索引
    （**只删索引，不动磁盘文件**）。
    """
    if threshold is not None and not (0.5 <= float(threshold) <= 1.0):
        raise HTTPException(400, "threshold 需在 0.5~1.0 之间")
    return _dup_groups(con, category=category, threshold=threshold)


@router.post("/media/duplicates/ignore")
def ignore_duplicate_group(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """「本组忽略」：被忽略的组以后不再出现在重复检测结果里。

    记录的是组内成员的**归一化标题**集合（存 settings）—— 即便组里某条被删掉，
    剩余成员仍会被识别为已忽略；把标题改掉则自然解除。``clear=true`` 清空全部忽略名单。
    """
    import json as _json
    body = body or {}
    keys = sorted(_load_dup_ignore(con))
    if body.get("clear"):
        keys = []
    else:
        key = (body.get("key") or "").strip()
        if not key:
            raise HTTPException(400, "缺少 key（组的归一化标题）")
        if body.get("undo"):
            keys = [k for k in keys if k != key]
        else:
            if key not in keys:
                keys.append(key)
            # 该组所有成员的归一化标题一并记录（按 key 找回该组）
            for g in _dup_groups(con)["groups"]:
                if g["key"] == key:
                    for it in g["items"]:
                        k2 = _dup_norm(it["title"] or it["title_jp"] or "")
                        if k2 and k2 not in keys:
                            keys.append(k2)
                    break
            keys = keys[:500]        # 名单上限保护
    db.set_setting(con, DUP_IGNORE_SETTINGS_KEY, _json.dumps(keys, ensure_ascii=False))
    con.commit()
    return {"ok": True, "ignored": len(keys)}


@router.post("/media/bulk")
def bulk_media(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """批量操作：移动分类 / 删除索引 / 打标签（**只改索引，绝不删除磁盘文件**）。

    action=move   ：把 ids 移到 category；
    action=delete ：删除这些索引及其关联数据（文件不动）；
    action=tag    ：给 ids 批量打标签（tags 为标签名数组，mode=append|replace|remove）。
    """
    body = body or {}
    action = (body.get("action") or "").strip().lower()
    ids = [int(x) for x in (body.get("ids") or []) if str(x).strip().isdigit()]
    if action not in ("move", "delete", "tag"):
        raise HTTPException(400, "action 只能是 move / delete / tag")
    if not ids:
        raise HTTPException(400, "未选择任何作品")
    ph = ",".join("?" * len(ids))
    found = [r["id"] for r in con.execute(
        f"SELECT id FROM media WHERE id IN ({ph})", ids).fetchall()]
    if action == "move":
        category = ((body.get("category") or "").strip())[:40]
        if not category:
            raise HTTPException(400, "移动需要提供目标分类")
        db.sync_categories(con)
        con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (category,))
        cur = con.execute(
            f"UPDATE media SET category=?, updated_at=datetime('now','localtime') "
            f"WHERE id IN ({ph})", [category, *found])
        con.commit()
        return {"ok": True, "action": "move", "moved": cur.rowcount, "category": category}
    if action == "tag":
        raw = body.get("tags") or []
        names = []
        for x in raw:
            nm = _normalize_tag_name(x)
            if nm and nm not in names:
                names.append(nm)
        if not names:
            raise HTTPException(400, "未提供有效标签（tags 为空或全部无效）")
        mode = (body.get("mode") or "append").strip().lower()
        if mode not in ("append", "replace", "remove"):
            raise HTTPException(400, "mode 只能是 append / replace / remove")
        stats = bulk_tag_media(con, found, names, mode)
        con.commit()
        return {"ok": True, "action": "tag", "mode": mode, "tags": names, **stats}
    # delete：仅删索引与关联（media_tags / watch_state / cover_reviews），文件不动
    deleted = 0
    for mid in found:
        con.execute("DELETE FROM media_tags WHERE media_id=?", (mid,))
        con.execute("DELETE FROM watch_state WHERE media_id=?", (mid,))
        con.execute("DELETE FROM cover_reviews WHERE media_id=?", (mid,))
        con.execute("DELETE FROM media WHERE id=?", (mid,))
        deleted += 1
    con.commit()
    return {"ok": True, "action": "delete", "deleted": deleted,
            "note": "仅删除索引，磁盘文件未做任何改动"}


@router.post("/media/delete-files")
def delete_media_with_files(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """删除作品：**索引 + 磁盘真实文件**（破坏性操作，前端必须二次确认）。

    - 视频文件：存在即删除（``os.remove``）；
    - 封面文件：仅当位于服务端管理的缓存目录（``frames_cache`` / ``cover_cache``）时一并删除，
      位于用户素材目录里的海报**保留**（避免误删可能被其它作品共用的图片），并在返回里列出；
    - 索引与关联数据（media_tags / watch_state / cover_reviews）同步删除；
    - 文件已不存在的只删索引。
    """
    import os as _os
    import shutil as _shutil

    body = body or {}
    ids = [int(x) for x in (body.get("ids") or []) if str(x).strip().isdigit()]
    if not ids:
        raise HTTPException(400, "未选择任何作品")
    ph = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, file_path, poster_path FROM media WHERE id IN ({ph})", ids).fetchall()
    if not rows:
        raise HTTPException(404, "作品不存在")

    cfg = cfg_mod.load()
    managed = []
    frames_dir = _os.path.abspath(cfg.get("frames_dir") or _os.path.join(cfg_mod.BASE, "frames_cache"))
    cover_cache = _os.path.abspath(_os.path.join(cfg_mod.BASE, "cover_cache"))
    managed += [frames_dir, cover_cache]

    deleted = files_deleted = missing = 0
    leftovers = []
    for r in rows:
        fp = r["file_path"] or ""
        # ① 删磁盘文件（先文件后索引，避免索引删了文件留孤儿还无从追溯）
        if fp and _os.path.exists(fp):
            if _os.path.isdir(fp):
                leftovers.append(fp + "（是目录，已跳过）")
            else:
                try:
                    _os.remove(fp)
                    files_deleted += 1
                except OSError as e:
                    leftovers.append(f"{fp}（删除失败：{e}）")
                    continue            # 文件删不掉 → 保留索引，避免出现"索引没了文件还在还删不掉"的混乱
        else:
            missing += 1
        poster = r["poster_path"] or ""
        if poster and _os.path.exists(poster):
            if any(_os.path.abspath(poster).startswith(d) for d in managed):
                try:
                    _os.remove(poster)
                except OSError:
                    leftovers.append(poster)
            else:
                leftovers.append(poster + "（素材目录海报，已保留）")
        # ② 删索引与关联
        con.execute("DELETE FROM media_tags WHERE media_id=?", (r["id"],))
        con.execute("DELETE FROM watch_state WHERE media_id=?", (r["id"],))
        con.execute("DELETE FROM cover_reviews WHERE media_id=?", (r["id"],))
        con.execute("DELETE FROM media WHERE id=?", (r["id"],))
        deleted += 1
    con.commit()
    # 清理空的 frames_cache 孤儿（不递归删除目录本身）
    return {"ok": True, "deleted": deleted, "files_deleted": files_deleted,
            "missing": missing, "leftovers": leftovers,
            "note": "已删除索引与磁盘文件；素材目录里的海报文件未删除" if leftovers
                    else "已删除索引与磁盘文件"}


@router.get("/media/{mid}")
def get_media_admin(mid: int, _u=Depends(require_admin), con=Depends(get_db)):
    m = media_or_404(con, mid)
    return {k: m[k] for k in m.keys()} | {"tags": media_tags(con, mid)}


# ------------------------------------------------------------- 联网补全（候选制）
@router.post("/media/{mid}/metadata-refresh")
def metadata_refresh(mid: int, _u=Depends(require_admin), con=Depends(get_db)):
    """触发联网补全（media-db 等），返回候选（不写库）。"""
    row = media_or_404(con, mid)
    cand = OnlineResolver().resolve({k: row[k] for k in row.keys()})
    cand["edited_fields"] = sorted(set(json.loads(row["edited_fields"] or "[]")))
    cand["editable"] = {
        "synopsis": "synopsis" not in (cand["edited_fields"]),
        "tags": "tags" not in (cand["edited_fields"]),
    }
    return {"media_id": mid, "candidate": cand}


@router.post("/media/{mid}/metadata-apply")
def metadata_apply(mid: int, body: dict, _u=Depends(require_admin), con=Depends(get_db)):
    """人工审定后应用候选：简介/tag 入库；已人工编辑过的字段不被覆盖。"""
    m = media_or_404(con, mid)
    edited = set(json.loads(m["edited_fields"] or "[]"))
    cand = validate_candidate(body)

    if cand.get("synopsis") and "synopsis" not in edited:
        con.execute("UPDATE media SET synopsis=?, edited_fields=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (cand["synopsis"],
                     json.dumps(sorted(edited | {"synopsis"}), ensure_ascii=False), mid))
        edited.add("synopsis")
    if cand.get("tags") and "tags" not in edited:
        set_media_tags(con, mid, cand["tags"][:TAG_LIMIT])  # 每作品最多 TAG_LIMIT 个
        edited.add("tags")
        con.execute("UPDATE media SET edited_fields=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (json.dumps(sorted(edited), ensure_ascii=False), mid))
    con.commit()
    return {"ok": True, "media": {k: m[k] for k in m.keys()} | {"tags": media_tags(con, mid)}}


# ---------------------------------------------------------------- 长任务（扫描 / 联网补全）
# 复用一个全局互斥槽：同一时刻只允许一个长任务（扫描 or 全量补全）运行，
# 任务对象支持进度回报与取消。
def _current_job():
    j = jobs.current()
    return j.status() if j is not None else {"running": False}


def _cleanup_dirty_data(kind: str) -> None:
    """终止任务后清理其产生的缓存文件与脏数据。

    - cover-online：删除待审定候选（cover_reviews pending）+ 清理 cover_cache 孤儿文件；
    - 其余任务：无临时文件，仅停止执行。
    """
    if kind != "cover-online":
        return
    cfg = cfg_mod.load()
    cover_dir = os.path.join(cfg_mod.BASE, "cover_cache")
    con = db.connect()
    db.init(con)
    try:
        con.execute("DELETE FROM cover_reviews WHERE status='pending'")
        con.commit()
        if os.path.isdir(cover_dir):
            referenced = set(r[0] for r in con.execute(
                "SELECT poster_path FROM media WHERE poster_path IS NOT NULL").fetchall())
            for fn in os.listdir(cover_dir):
                p = os.path.abspath(os.path.join(cover_dir, fn))
                if os.path.isfile(p) and p not in referenced:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    finally:
        con.close()


@router.post("/scan")
def trigger_scan(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """启动扫描：scope=full 全盘 / scope=path 定点目录。拆分目录分块，可取消。

    **两段式**：扫描只产出「待导入清单」（不写库），人工在下方确认清单并点「导入到索引」才会入库。
    category：可选。指定分类时，扫描清单会按该分类预标注（导入时仍以导入时选择的分类为准）。
    全盘扫描须显式确认（防误触），未确认一律拒绝。
    """
    body = body or {}
    scope = body.get("scope", "full")
    path = body.get("path")
    category = (body.get("category") or "").strip() or None
    if scope == "path" and not path:
        raise HTTPException(400, "scope=path 需要提供 path")
    if scope == "full" and not body.get("confirm_full"):
        raise HTTPException(400, "全盘扫描需确认（confirm_full=true）")
    if category:
        db.sync_categories(con)
        con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (category,))
        con.commit()
    job = jobs.Job("scan", _notify_wrap(_WORKER_LABELS["scan"], _scan_worker),
                   scope=scope, path=path, category=category)
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True, "scope": scope, "path": path, "category": category}


@router.get("/scan/status")
def scan_status(_u=Depends(require_admin)):
    j = jobs.current()
    if j is None or j.kind != "scan":
        return {"running": False, "kind": None}
    return j.status()


@router.get("/scan/staged")
def scan_staged(limit: int = Query(200, le=1000), offset: int = 0,
                _u=Depends(require_admin)):
    """当前「待导入清单」：扫描产出、尚未人工导入的候选（仅内存暂存，重启即清空）。"""
    from ..services import scan_stage
    s = scan_stage.summary()
    s["items"] = scan_stage.view(limit=limit, offset=offset)
    s["limit"] = limit
    s["offset"] = offset
    return s


@router.post("/scan/staged/clear")
def scan_staged_clear(_u=Depends(require_admin)):
    """放弃当前待导入清单（不写库，仅清空暂存）。"""
    from ..services import scan_stage
    return {"ok": True, "cleared": scan_stage.clear()}


@router.post("/scan/import")
def scan_import(body: dict = None, _u=Depends(require_admin)):
    """把待导入清单真正写入索引（**人工确认后才会导入**）。

    防呆：``category`` 必填且不能是「自动（按目录名）」—— 导入归属哪个分类必须由人明确选择。
    """
    from ..services import scan_stage

    body = body or {}
    category = (body.get("category") or "").strip()
    if not category or category in ("自动", "自动（按目录名）"):
        raise HTTPException(400, "请先选择导入分类（不能选「自动（按目录名）」）")
    staged = scan_stage.summary()
    if staged["total"] == 0:
        raise HTTPException(400, "暂无待导入清单，请先执行扫描")
    job = jobs.Job("scan", _notify_wrap(_WORKER_LABELS["scan"], _scan_import_worker),
                   scope="import", phase="import", category=category, total=staged["total"])
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True, "category": category, "total": staged["total"],
            "added": staged["added"], "updated": staged["updated"]}


@router.post("/scan/cancel")
def cancel_scan(_u=Depends(require_admin)):
    j = jobs.current()
    if j is not None and j.kind == "scan" and j.is_running:
        j.cancel()
        return {"ok": True, "msg": "已请求终止，将在当前目录处理完后停止"}
    return {"ok": False, "msg": "当前没有正在运行的扫描"}


@router.post("/completion")
def trigger_completion(body: dict = None, _u=Depends(require_admin)):
    """联网补全：全量（缺简介）/ 指定 mid / 指定 ids 列表。候选制，尊重人工编辑字段。"""
    body = body or {}
    mid = body.get("mid")
    ids = body.get("ids")
    job = jobs.Job("completion", _notify_wrap(_WORKER_LABELS["completion"], _completion_worker),
                   source=body.get("source", "online"), mid=mid, ids=ids)
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True, "one": mid is not None, "ids": bool(ids)}


@router.get("/completion/status")
def completion_status(_u=Depends(require_admin)):
    j = jobs.current()
    if j is None or j.kind != "completion":
        return {"running": False, "kind": None}
    return j.status()


@router.post("/completion/cancel")
def cancel_completion(_u=Depends(require_admin)):
    j = jobs.current()
    if j is not None and j.kind == "completion" and j.is_running:
        j.cancel()
        return {"ok": True, "msg": "已请求终止，将在当前作品处理完后停止"}
    return {"ok": False, "msg": "当前没有正在运行的补全"}


# ------------------------------------------------- 文件夹年份/月份信息回填
@router.post("/metadata-backfill")
def trigger_metadata_backfill(_u=Depends(require_admin)):
    job = jobs.Job("metadata", _notify_wrap(_WORKER_LABELS["metadata"], _metadata_worker))
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True}


@router.get("/metadata-backfill/status")
def metadata_backfill_status(_u=Depends(require_admin)):
    j = jobs.current()
    if j is None or j.kind != "metadata":
        return {"running": False, "kind": None}
    return j.status()


@router.post("/metadata-backfill/cancel")
def cancel_metadata_backfill(_u=Depends(require_admin)):
    j = jobs.current()
    if j is not None and j.kind == "metadata" and j.is_running:
        j.cancel()
        return {"ok": True, "msg": "已请求终止"}
    return {"ok": False, "msg": "当前没有正在运行的信息回填"}


# ------------------------------------------------- 封面补全（一次性任务）
@router.post("/cover")
def trigger_cover_backfill(_u=Depends(require_admin)):
    job = jobs.Job("cover", _notify_wrap(_WORKER_LABELS["cover"], _cover_worker))
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True}


@router.get("/cover/status")
def cover_status(_u=Depends(require_admin)):
    j = jobs.current()
    if j is None or j.kind != "cover":
        return {"running": False, "kind": None}
    return j.status()


@router.post("/cover/cancel")
def cancel_cover_backfill(_u=Depends(require_admin)):
    j = jobs.current()
    if j is not None and j.kind == "cover" and j.is_running:
        j.cancel()
        _cleanup_dirty_data("cover")
        return {"ok": True, "msg": "已请求终止"}
    return {"ok": False, "msg": "当前没有正在运行的封面补全"}


# ------------------------------------------------- 联网封面补全（磁盘无图作品，联网搜索下载）
@router.post("/cover-online")
def trigger_cover_online(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """联网搜索封面：必须先确定范围（由「A · 全量联网补全」解析出的索引ID）。"""
    ids = [int(x) for x in ((body or {}).get("ids") or []) if str(x).strip().isdigit()]
    if not ids:
        return {"started": False, "reason": "需先确定范围：请到「A · 全量联网补全」上传并解析索引ID"
                                           "（作品编辑 → 下载索引ID.txt）后再执行联网搜索封面"}
    # 记录本次运行范围：供「C · 人工审定」的「刷新列表」按步骤B的结果筛选
    db.set_setting(con, "cover_online_scope", json.dumps(ids))
    db.set_setting(con, "cover_online_scope_ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    job = jobs.Job("cover-online", _notify_wrap(_WORKER_LABELS["cover-online"], _cover_online_worker),
                   ids=ids)
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True, "count": len(ids)}


@router.post("/cover-dir")
def trigger_cover_dir(body: dict = None, _u=Depends(require_admin)):
    """指定目录补全：从用户选择的本地目录取图片，与缺封面作品按标题匹配并写回封面路径。

    只写路径引用（不复制/移动/重命名任何文件），并尊重人工编辑过的 poster_path。
    """
    path = ((body or {}).get("path") or "").strip().strip('"')
    if not path:
        raise HTTPException(400, "请填写一个本地目录路径")
    if not os.path.isdir(path):
        raise HTTPException(400, f"目录不存在或不可访问：{path}")
    ids = [int(x) for x in ((body or {}).get("ids") or []) if str(x).strip().isdigit()]
    r = run_cover_dir_backfill(cfg_mod.load(), path, ids=ids)
    return {"ok": True, **r}


@router.post("/cover-frame")
def trigger_cover_frame(body: dict = None, _u=Depends(require_admin)):
    """服务端抽帧封面：用 ffmpeg 给缺封面的作品抽帧落盘小图，写 poster_path。

    body: {ids: [...]} **必填** —— 使用 **A 区解析出的索引ID**（与「联网搜索封面」同一范围来源），
    只处理范围内当前没有封面的作品；未传 ids 一律拒绝，避免误扫全库、耗时过长。
    抽帧后前端走 /api/poster/{id} 普通图片加载，无需浏览器再抓帧（首屏更快）。
    （旧版「截图视频封面（标记）」已移除，由本功能替代。）
    """
    body = body or {}
    ids = []
    for x in (body.get("ids") or []):
        if str(x).strip().isdigit() and int(x) not in ids:
            ids.append(int(x))
    if not ids:
        raise HTTPException(400, "请先在 A 区解析出索引ID（待处理范围），再执行服务端抽帧")
    job = jobs.Job("frame", _notify_wrap(_WORKER_LABELS["frame"], _frame_worker), ids=ids)
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True, "count": len(ids) if ids else None}


@router.get("/cover-online/status")
def cover_online_status(_u=Depends(require_admin)):
    j = jobs.current()
    if j is None or j.kind != "cover-online":
        return {"running": False, "kind": None}
    return j.status()


@router.post("/cover-online/cancel")
def cancel_cover_online(_u=Depends(require_admin)):
    j = jobs.current()
    if j is not None and j.kind == "cover-online" and j.is_running:
        j.cancel()
        _cleanup_dirty_data("cover-online")
        return {"ok": True, "msg": "已请求终止，正在清理缓存与待审定脏数据"}
    return {"ok": False, "msg": "当前没有正在运行的联网封面补全"}


# ------------------------------------------------- 封面人工审定（低置信度 / 无源候选）
_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
         "webp": "image/webp", "bmp": "image/bmp", "gif": "image/gif"}


def _review_row(con, rid: int):
    r = con.execute(
        "SELECT r.*, m.title, m.title_jp, m.file_path, m.poster_path "
        "FROM cover_reviews r JOIN media m ON m.id=r.media_id WHERE r.id=?",
        (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "审定记录不存在")
    return r


def _review_dict(r) -> dict:
    d = {k: r[k] for k in r.keys()}
    try:
        d["candidates"] = json.loads(r["candidates"] or "[]")
    except ValueError:
        d["candidates"] = []
    return d


@router.get("/cover-reviews")
def list_cover_reviews(status: str = "pending", source: str = "",
                       _u=Depends(require_admin), con=Depends(get_db)):
    """列出待人工审定的封面候选（默认 pending）。

    source="last_cover_online"：只列出**最近一次「B · 联网搜索封面」范围**内的作品，
    即按步骤 B 的结果筛选（无记录则不过滤，并在响应里说明）。
    """
    rows = con.execute(
        "SELECT r.*, m.title, m.title_jp, m.file_path, m.poster_path "
        "FROM cover_reviews r JOIN media m ON m.id=r.media_id "
        "WHERE r.status=? ORDER BY r.created_at DESC, r.id DESC",
        (status,)).fetchall()
    items = [_review_dict(r) for r in rows]
    scope_ids, scope_ts, filtered = 0, None, False
    if source == "last_cover_online":
        raw = db.get_setting(con, "cover_online_scope")
        scope_ts = db.get_setting(con, "cover_online_scope_ts")
        try:
            ids = {int(x) for x in json.loads(raw)} if raw else set()
        except ValueError:
            ids = set()
        scope_ids = len(ids)
        if ids:
            before = len(items)
            items = [it for it in items if it["media_id"] in ids]
            filtered = True
            dropped = before - len(items)
        else:
            dropped = 0
    else:
        dropped = 0
    return {"items": items, "source": source or "all",
            "scope_ids": scope_ids, "scope_ts": scope_ts,
            "filtered": filtered, "dropped": dropped,
            "total_kept": len(items)}


@router.get("/cover-reviews/{rid}/preview/{idx}")
def cover_review_preview(rid: int, idx: int, _u=Depends(require_admin), con=Depends(get_db)):
    """代理加载候选图（经服务器转发，避免浏览器直连来源站被防盗链拦截）。"""
    r = _review_row(con, rid)
    cands = _review_dict(r)["candidates"]
    if idx < 0 or idx >= len(cands):
        raise HTTPException(404, "候选不存在")
    url = cands[idx]
    try:
        data = _fetch(url, referer="https://media-db.example.com/")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"候选图加载失败: {e}") from e
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower().lstrip(".")
    return Response(content=data, media_type=_MIME.get(ext, "application/octet-stream"))


@router.post("/cover-reviews/{rid}/accept")
def accept_cover_review(rid: int, body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """人工确认采用某候选：下载到 cover_cache 并写回 poster_path（记为人工编辑）。"""
    r = _review_row(con, rid)
    if r["status"] != "pending":
        raise HTTPException(400, "该记录已处理")
    cands = _review_dict(r)["candidates"]
    url = (body or {}).get("url")
    if url:
        if url not in cands:
            raise HTTPException(400, "候选不在列表中")
        chosen = [url]
    else:
        chosen = cands
    if not chosen:
        raise HTTPException(400, "该记录没有候选图，请先重搜")

    m = media_or_404(con, r["media_id"])
    cover_dir = os.path.join(cfg_mod.BASE, "cover_cache")
    os.makedirs(cover_dir, exist_ok=True)
    path, err = download_cover(dict(m), chosen, cover_dir)
    if not path:
        raise HTTPException(502, f"下载失败: {err}")

    meta = json.loads(m["meta"] or "{}")
    meta["online_cover"] = {
        "url": url or chosen[0], "post": r["post_url"], "source": "media-db.example.com",
        "confidence": r["confidence"], "manual_review": True,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    edited = set(json.loads(m["edited_fields"] or "[]"))
    edited.add("poster_path")
    con.execute(
        "UPDATE media SET poster_path=?, meta=?, edited_fields=?, "
        "updated_at=datetime('now','localtime') WHERE id=?",
        (path, json.dumps(meta, ensure_ascii=False),
         json.dumps(sorted(edited), ensure_ascii=False), r["media_id"]))
    con.execute(
        "DELETE FROM cover_reviews WHERE id=?",
        (rid,))
    con.commit()
    return {"ok": True, "media_id": r["media_id"], "poster_path": path}


@router.post("/cover-reviews/batch-accept")
def batch_accept_cover_review(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """批量采纳：一次请求处理多条 {review_id, url}，避免逐个请求。"""
    items = (body or {}).get("items") or []
    if not items:
        raise HTTPException(400, "未选择任何审定记录")
    ok_count = 0
    failed = []
    for it in items:
        rid = it.get("review_id")
        url = it.get("url")
        try:
            r = _review_row(con, rid)
            if r["status"] != "pending":
                failed.append({"review_id": rid, "error": "该记录已处理"})
                continue
            cands = _review_dict(r)["candidates"]
            if not cands:
                failed.append({"review_id": rid, "error": "该记录没有候选图"})
                continue
            chosen = [url] if (url and url in cands) else cands
            m = media_or_404(con, r["media_id"])
            cover_dir = os.path.join(cfg_mod.BASE, "cover_cache")
            os.makedirs(cover_dir, exist_ok=True)
            path, err = download_cover(dict(m), chosen, cover_dir)
            if not path:
                failed.append({"review_id": rid, "error": f"下载失败: {err}"})
                continue
            meta = json.loads(m["meta"] or "{}")
            meta["online_cover"] = {
                "url": url or chosen[0], "post": r["post_url"], "source": "media-db.example.com",
                "confidence": r["confidence"], "manual_review": True,
                "ts": time.strftime("%Y-%m-%d %H:%M"),
            }
            edited = set(json.loads(m["edited_fields"] or "[]"))
            edited.add("poster_path")
            con.execute(
                "UPDATE media SET poster_path=?, meta=?, edited_fields=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (path, json.dumps(meta, ensure_ascii=False),
                 json.dumps(sorted(edited), ensure_ascii=False), r["media_id"]))
            con.execute(
                "DELETE FROM cover_reviews WHERE id=?",
                (rid,))
            ok_count += 1
        except Exception as e:  # noqa: BLE001
            failed.append({"review_id": rid, "error": f"{e}"})
    con.commit()
    return {"ok": True, "count": ok_count, "failed": failed}


@router.post("/cover-reviews/batch-submit")
def batch_submit_cover_review(body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """批量提交：采纳选中素材（写回封面）或拒绝（删除记录）。一次请求处理多条。"""
    accept_items = (body or {}).get("accept") or []
    reject_ids = (body or {}).get("reject") or []
    if not accept_items and not reject_ids:
        raise HTTPException(400, "未选择任何审定记录")
    accepted = rejected = 0
    failed = []
    for it in accept_items:
        rid = it.get("review_id")
        url = it.get("url")
        try:
            r = _review_row(con, rid)
            if r["status"] != "pending":
                failed.append({"review_id": rid, "error": "该记录已处理"})
                continue
            cands = _review_dict(r)["candidates"]
            if not cands:
                failed.append({"review_id": rid, "error": "该记录没有候选图"})
                continue
            chosen = [url] if (url and url in cands) else cands
            m = media_or_404(con, r["media_id"])
            cover_dir = os.path.join(cfg_mod.BASE, "cover_cache")
            os.makedirs(cover_dir, exist_ok=True)
            path, err = download_cover(dict(m), chosen, cover_dir)
            if not path:
                failed.append({"review_id": rid, "error": f"下载失败: {err}"})
                continue
            meta = json.loads(m["meta"] or "{}")
            meta["online_cover"] = {
                "url": url or chosen[0], "post": r["post_url"], "source": "media-db.example.com",
                "confidence": r["confidence"], "manual_review": True,
                "ts": time.strftime("%Y-%m-%d %H:%M"),
            }
            edited = set(json.loads(m["edited_fields"] or "[]"))
            edited.add("poster_path")
            con.execute(
                "UPDATE media SET poster_path=?, meta=?, edited_fields=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (path, json.dumps(meta, ensure_ascii=False),
                 json.dumps(sorted(edited), ensure_ascii=False), r["media_id"]))
            con.execute("DELETE FROM cover_reviews WHERE id=?", (rid,))
            accepted += 1
        except Exception as e:  # noqa: BLE001
            failed.append({"review_id": rid, "error": f"{e}"})
    for rid in reject_ids:
        try:
            con.execute("DELETE FROM cover_reviews WHERE id=?", (rid,))
            rejected += 1
        except Exception as e:  # noqa: BLE001
            failed.append({"review_id": rid, "error": f"{e}"})
    con.commit()
    return {"ok": True, "accepted": accepted, "rejected": rejected, "failed": failed}


@router.post("/cover-reviews/{rid}/reject")
def reject_cover_review(rid: int, _u=Depends(require_admin), con=Depends(get_db)):
    """拒绝该候选：删除记录（不再展示）。"""
    r = _review_row(con, rid)
    con.execute("DELETE FROM cover_reviews WHERE id=?", (rid,))
    con.commit()
    return {"ok": True, "media_id": r["media_id"]}


@router.post("/cover-reviews/{rid}/rescan")
def rescan_cover_review(rid: int, body: dict = None, _u=Depends(require_admin), con=Depends(get_db)):
    """用自定义关键词重新联网搜索，刷新该作品的候选（供无源 / 候选不理想时调整）。"""
    r = _review_row(con, rid)
    if r["status"] != "pending":
        raise HTTPException(400, "该记录已处理")
    keyword = ((body or {}).get("keyword") or "").strip() or None  # 留空回退标题派生
    m = media_or_404(con, r["media_id"])
    cand = search_cover(dict(m), keyword=keyword)
    save_review(con, r["media_id"], cand)
    con.commit()
    new = con.execute(
        "SELECT r.*, m.title, m.title_jp, m.file_path, m.poster_path "
        "FROM cover_reviews r JOIN media m ON m.id=r.media_id "
        "WHERE r.media_id=? AND r.status='pending'", (r["media_id"],)).fetchone()
    return {"ok": True, "review": _review_dict(new) if new else None}


# ------------------------------------------------- 本机简评打标（题材词典，临时方案）
@router.post("/tags-backfill")
def trigger_tags_backfill(body: dict = None, _u=Depends(require_admin)):
    force = bool((body or {}).get("force"))  # 恢复场景：忽略 edited_fields 保护
    job = jobs.Job("tags", _notify_wrap(_WORKER_LABELS["tags"], _tags_worker), force=force)
    r = jobs.run_exclusive(job)
    if not r["started"]:
        return {"started": False, "reason": r["reason"]}
    return {"started": True}


@router.get("/tags-backfill/status")
def tags_backfill_status(_u=Depends(require_admin)):
    j = jobs.current()
    if j is None or j.kind != "tags":
        return {"running": False, "kind": None}
    return j.status()


@router.post("/tags-backfill/cancel")
def cancel_tags_backfill(_u=Depends(require_admin)):
    j = jobs.current()
    if j is not None and j.kind == "tags" and j.is_running:
        j.cancel()
        return {"ok": True, "msg": "已请求终止"}
    return {"ok": False, "msg": "当前没有正在运行的打标任务"}


@router.get("/task")
def any_task_status(_u=Depends(require_admin)):
    return _current_job()


# ---- 消息通知 API（路由顺序：静态路径在 {nid} 之前）----
@router.get("/notifications")
def list_notifications(limit: int = 50, unread_only: int = 0,
                       _u=Depends(require_admin), con=Depends(get_db)):
    where = "WHERE is_read=0" if unread_only else ""
    rows = con.execute(
        f"SELECT id, type, title, body, is_read, created_at FROM notifications {where} "
        f"ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/notifications/unread-count")
def notifications_unread_count(_u=Depends(require_admin), con=Depends(get_db)):
    c = con.execute("SELECT COUNT(*) FROM notifications WHERE is_read=0").fetchone()[0]
    return {"count": c}


@router.post("/notifications/read-all")
def notifications_read_all(_u=Depends(require_admin), con=Depends(get_db)):
    con.execute("UPDATE notifications SET is_read=1 WHERE is_read=0")
    con.commit()
    return {"ok": True}


@router.get("/notifications/{nid}")
def get_notification(nid: int, _u=Depends(require_admin), con=Depends(get_db)):
    r = con.execute(
        "SELECT id, type, title, body, is_read, created_at FROM notifications WHERE id=?",
        (nid,)).fetchone()
    if r is None:
        raise HTTPException(404, "通知不存在")
    return dict(r)


@router.post("/notifications/{nid}/read")
def mark_notification_read(nid: int, _u=Depends(require_admin), con=Depends(get_db)):
    con.execute("UPDATE notifications SET is_read=1 WHERE id=?", (nid,))
    con.commit()
    return {"ok": True}


@router.get("/pending")
def pending_ratings(_u=Depends(require_admin)):
    j = jobs.current()
    res = j.status().get("result") or {} if j is not None else {}
    return {"pending": res.get("rating_pending", []), "note": "最近一次扫描的待人工匹配简评"}


@router.get("/config")
def view_config(_u=Depends(require_admin)):
    cfg = cfg_mod.load()
    return {k: cfg[k] for k in ("roots", "category_filter", "db_path", "memory_guard_bytes", "port")}


# ---------------------------------------------------------------- 一键重启（防误触）
@router.get("/restart-token")
def restart_token(_u=Depends(require_admin), con=Depends(get_db)):
    token = secrets.token_hex(16)
    db.set_setting(con, "restart_token", token)
    return {"token": token}


@router.post("/restart")
def restart(_u=Depends(require_admin), body: dict = None, con=Depends(get_db)):
    body = body or {}
    if body.get("confirm_text") != RESTART_CONFIRM:
        raise HTTPException(400, "确认文本不正确")
    if body.get("token") != db.get_setting(con, "restart_token"):
        raise HTTPException(400, "验证 token 无效，请刷新后重试")

    cfg = cfg_mod.load()
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", cfg["host"], "--port", str(cfg["port"])]
    _push_notification("info", "服务重启", "管理员已触发一键重启，服务即将重新拉起。")

    def _do():
        time.sleep(1.0)
        # 让当前进程把手头连接收尾后，换成本身命令重新拉起
        subprocess.Popen(cmd, cwd=cfg_mod.BASE, close_fds=True)
        os._exit(0)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "msg": "即将重启服务，约数秒后恢复"}