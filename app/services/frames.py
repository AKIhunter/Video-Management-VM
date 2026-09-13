"""服务端 ffmpeg 预抽帧：把「视频预览帧封面」从浏览器抓帧改为服务端落盘小图。

背景：前端离屏 <video> 抓帧受网络 metadata + 解码瓶颈，首屏慢。改为服务端用 ffmpeg
一次性抽帧存 jpg 写入 poster_path，前端走 /api/poster/{id} 普通图片并发加载（30 张 <500ms）。

- ffmpeg_exe()：抽象 ffmpeg 来源（config.ffmpeg_path 优先，否则 imageio-ffmpeg 捆绑）。
- video_duration()：ffmpeg -i 解析时长（用于按比例定位帧）。
- extract_frame()：调 ffmpeg 抽一帧存 jpg（等比缩到固定宽）。
- is_black_image()：PIL 判亮度，与前端 frameStats 口径一致。
- pick_bright_frame()：抽 20% 处一帧，全黑则回退 50%（最多 2 次）。
- run_frame_backfill()：长任务 worker，给缺封面的作品（按 A 区解析的索引ID 限定范围）抽帧写 poster_path。
"""
import json as _json
import logging
import os
import re
import subprocess

from .. import config as cfg_mod
from .. import db

log = logging.getLogger("vm.frames")

FRAME_WIDTH = 512   # 抽帧目标宽度（等比缩放，覆盖网格/详情展示足够）


def ffmpeg_exe(cfg=None) -> str:
    """返回 ffmpeg 可执行路径：优先 config.ffmpeg_path，否则 imageio-ffmpeg 捆绑。"""
    cfg = cfg or cfg_mod.load()
    exe = (cfg.get("ffmpeg_path") or "").strip()
    if exe and os.path.exists(exe):
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        log.warning("imageio_ffmpeg 不可用：%r", e)
        return exe


def frame_dir(cfg=None) -> str:
    """抽帧小图缓存目录（确保存在）。独立于联网封面 cover_cache。"""
    cfg = cfg or cfg_mod.load()
    d = cfg.get("frames_dir") or "frames_cache"
    if not os.path.isabs(d):
        d = os.path.join(cfg_mod.BASE, d)
    os.makedirs(d, exist_ok=True)
    return d


def video_duration(video_path: str) -> float:
    """用 ffmpeg -i 解析视频时长（秒）。失败返回 0。"""
    exe = ffmpeg_exe()
    if not exe:
        return 0.0
    try:
        r = subprocess.run([exe, "-i", video_path], capture_output=True, timeout=60)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)",
                      r.stderr.decode("utf-8", "ignore"))
        if m:
            h, mi, s, cs = map(int, m.groups())
            return h * 3600 + mi * 60 + s + cs / 100.0
    except Exception:
        pass
    return 0.0


def extract_frame(video_path: str, at_sec: float, out_jpg: str,
                  width: int = FRAME_WIDTH) -> bool:
    """抽 video_path 在 at_sec 处的一帧，等比缩到 width 宽，存 out_jpg。返回是否成功。"""
    exe = ffmpeg_exe()
    if not exe or not os.path.exists(video_path):
        log.warning("extract_frame 前置失败：exe=%r video_exists=%s",
                    exe, os.path.exists(video_path))
        return False
    cmd = [exe, "-y", "-ss", f"{at_sec:.2f}", "-i", video_path,
           "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", out_jpg]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=90)
        ok = r.returncode == 0 and os.path.exists(out_jpg)
        if not ok:
            log.warning("ffmpeg 失败 rc=%s stderr=%s", r.returncode,
                        r.stderr.decode("utf-8", "ignore")[-300:])
        return ok
    except Exception as e:  # noqa: BLE001
        log.warning("ffmpeg 异常 %r", e)
        return False


def is_black_image(path: str) -> bool:
    """用 PIL 判断图片是否接近全黑（均亮 <28 或 亮像素占比 <6%，与前端口径一致）。"""
    try:
        from PIL import Image
        img = Image.open(path).convert("L")
        px = list(img.getdata())
        n = len(px)
        if not n:
            return True
        mean = sum(px) / n
        bright = sum(1 for v in px if v > 40)
        return mean < 28 or (bright / n) < 0.06
    except Exception:
        return False  # 读不了就当非黑，宁可用它


def pick_bright_frame(video_path: str, out_jpg: str) -> bool:
    """抽一帧非黑图：先 20%，全黑则 50%（最多 2 次 ffmpeg 调用）。返回是否成功。"""
    if not os.path.exists(video_path):
        return False
    dur = video_duration(video_path)
    times = [max(2.0, dur * 0.2)] if dur and dur > 0 else [5.0]
    if dur and dur > 0:
        times.append(max(2.0, dur * 0.5))
    else:
        times.append(90.0)
    for t in times:
        if not extract_frame(video_path, t, out_jpg):
            continue
        if not is_black_image(out_jpg):
            return True
    return os.path.exists(out_jpg)   # 两次都黑也保留最后一张（至少不是空）


def run_frame_backfill(job, ids=None) -> dict:
    """长任务 worker：给缺封面的作品用 ffmpeg 抽帧，落盘小图写 poster_path。

    ``ids``：限定范围（白屏管理按 **A 区解析出的索引ID** 传入，只处理其中没有封面的作品）；
    为 None 时处理全库缺封面作品（仅限脚本 / 兼容旧调用，界面入口已强制传 ids）。
    旧版「截图视频封面（标记）」已移除：本任务不再要求 meta.cover_mode=video_frame，
    处理成功后会顺手清掉遗留的该标记（前端改走普通图片加载）。
    """
    cfg = cfg_mod.load()
    id_list = []
    for x in (ids or []):
        if str(x).strip().isdigit() and int(x) not in id_list:
            id_list.append(int(x))
    id_set = set(id_list) or None
    out_dir = frame_dir(cfg)
    con = db.connect()
    db.init(con)
    try:
        if id_set is not None:
            ph = ",".join("?" * len(id_list))
            rows = con.execute(
                f"SELECT id, file_path, poster_path, meta FROM media WHERE id IN ({ph})",
                id_list).fetchall()
        else:
            rows = con.execute(
                "SELECT id, file_path, poster_path, meta FROM media").fetchall()
        targets = []
        skipped = 0
        for r in rows:
            if id_set is not None and r["id"] not in id_set:
                skipped += 1
                continue
            if r["poster_path"] and os.path.exists(r["poster_path"]):
                skipped += 1
                continue
            if not os.path.exists(r["file_path"]):
                skipped += 1
                continue
            targets.append(r)

        total = len(targets)
        extracted = failed = 0
        for i, r in enumerate(targets):
            if job.stop.is_set():
                break
            out = os.path.join(out_dir, f"{r['id']}.jpg")
            if not pick_bright_frame(r["file_path"], out):
                failed += 1
                log.warning("抽帧失败 #%s", r["id"])
            else:
                meta = {}
                try:
                    meta = _json.loads(r["meta"] or "{}") or {}
                except ValueError:
                    meta = {}
                meta.pop("cover_mode", None)   # 已落盘实体封面，前端改走 img（顺便清掉旧版标记）
                con.execute(
                    "UPDATE media SET poster_path=?, meta=?, updated_at=datetime('now','localtime') "
                    "WHERE id=?", (out, _json.dumps(meta, ensure_ascii=False), r["id"]))
                extracted += 1
            # 处理完一条再回报（此前用 0 起始的 i，导致进度条永远停在 (total-1)/total，看起来卡在一半）
            job.tick(i + 1, total, current=r["id"])
            if i % 20 == 0:
                con.commit()
        con.commit()
        log.info("抽帧完成：目标 %d · 成功 %d · 失败 %d · 跳过 %d",
                 total, extracted, failed, skipped)
        return {"total": total, "extracted": extracted, "failed": failed,
                "skipped": skipped, "canceled": bool(job.stop.is_set()),
                "scope_ids": len(id_set) if id_set else 0}
    finally:
        con.close()
