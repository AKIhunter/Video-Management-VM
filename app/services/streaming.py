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
    """内存/远程守卫建议：大文件或远程场景给出处置方式。"""
    size = media_row["file_size"] or 0
    oversized = size > cfg.get("memory_guard_bytes", 2147483648)
    return {
        "oversized": bool(oversized),
        "file_exists": bool(media_row["file_path"] and os.path.exists(media_row["file_path"])),
        "size": size,
        "advice": "建议在服务器本地打开，避免大文件流式占用内存" if oversized else "可直接流式在线播放",
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