"""文件流式输出（Range 支持）+ 内存/远程守卫 + 本地播放器回退。"""
import os

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

CHUNK = 1024 * 1024  # 1MB


def _media_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".wmv": "video/x-ms-wmv",
        ".flv": "video/x-flv",
    }.get(ext, "application/octet-stream")


def stream_file(path: str, request: Request, chunk=CHUNK) -> Response:
    size = os.path.getsize(path)
    rng = request.headers.get("range")
    status = 200
    start, end = 0, size - 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": _media_type(path),
    }
    if rng and rng.startswith("bytes="):
        try:
            a, b = rng[6:].split("-", 1)
            start = int(a) if a else 0
            end = int(b) if b else size - 1
            if start > end or start >= size:
                return Response(status_code=416, content="",
                                headers={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            status = 206
            length = end - start + 1
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(length)
            f = open(path, "rb")
            f.seek(start)
            return StreamingResponse(_iter_chunk(f, length), status_code=status, headers=headers)
        except Exception:
            f = open(path, "rb")
            headers["Content-Length"] = str(size)
            return StreamingResponse(_iter_chunk(f, size), status_code=200, headers=headers)
    headers["Content-Length"] = str(size)
    f = open(path, "rb")
    return StreamingResponse(_iter_chunk(f, size), status_code=200, headers=headers)


def _iter_chunk(f, length):
    try:
        remaining = length
        while remaining > 0:
            data = f.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
    finally:
        f.close()


def guard_advice(media_row, cfg) -> dict:
    """内存/远程守卫建议：**仅超过 2GB**（``memory_guard_bytes`` 默认 2147483648）的大文件建议本地播放。"""
    size = media_row["file_size"] or 0
    oversized = size > cfg.get("memory_guard_bytes", 2147483648)
    return {
        "oversized": bool(oversized),
        "file_exists": bool(media_row["file_path"] and os.path.exists(media_row["file_path"])),
        "size": size,
        "advice": "文件超过 2GB，建议本地播放，避免大文件流式占用内存" if oversized else "可直接流式在线播放",
    }


def open_local(path: str) -> bool:
    """在服务器（本机）用系统默认应用打开视频。仅对服务端本地文件有效。"""
    if not path or not os.path.exists(path):
        return False
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 — 打开的是配置库内已知存在的本地媒体
        return True
    import webbrowser
    webbrowser.open("file:///" + path.replace("\\", "/"))
    return True

# ---------------- 在线播放格式兼容 ----------------
# 浏览器（Chromium 系）可直接播放的容器/编码
NATIVE_EXTS = {".mp4", ".m4v", ".webm", ".mkv", ".mov", ".ogv", ".m4p"}
# 浏览器基本播不了的格式 → 由服务端 ffmpeg 实时转码为可流式的分片 mp4
TRANSCODE_EXTS = {".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".rmvb", ".rm",
                  ".vob", ".m2ts", ".ts", ".asf", ".divx", ".3gp"}


def needs_transcode(path: str) -> bool:
    """该文件是否需要服务端转码才能在浏览器播放（mkv/webm/mp4 等原生格式不需要）。"""
    return os.path.splitext(path or "")[1].lower() in TRANSCODE_EXTS


def stream_transcode(path: str, request: Request, cfg=None) -> Response:
    """用 ffmpeg 实时转码（avi / wmv / rmvb 等 → 分片 mp4），边转边流。

    - 转码为 H.264 + AAC、最长边 720p（veryfast），保证 CPU 可承受；
    - 分片 mp4（``-movflags frag_keyframe+empty_mooc``）无需 moov 原子即可边下边播；
    - 不支持拖动进度条（实时转码没有字节↔时间的映射），响应头显式声明 ``Accept-Ranges: none``；
    - 客户端断开时立即杀掉 ffmpeg 子进程，避免僵尸进程占用 CPU。
    """
    from fastapi import HTTPException
    from . import frames as frames_mod

    exe = frames_mod.ffmpeg_exe(cfg)
    if not exe:
        raise HTTPException(500, "未找到 ffmpeg，无法转码该格式（可配置 config.ffmpeg_path）")
    cmd = [
        exe, "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-vf", "scale=-2:720",
        "-c:a", "aac", "-ac", "2",
        "-f", "mp4", "-movflags", "frag_keyframe+empty_mooc",
        "pipe:1",
    ]
    import subprocess
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _gen():
        try:
            while True:
                chunk = proc.stdout.read(512 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001 — 进程可能已自行退出
                pass

    return StreamingResponse(
        _gen(),
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "none",
            "X-Transcode": "1",
            "Cache-Control": "no-store",
        })
