"""应用配置：读写项目根目录 config.json，并提供默认值与进程级缓存。

- DEFAULTS 兜底缺失键；load() 读文件 + 缓存（save() 时刷新缓存）。
- 相对路径目录（log_dir / frames_dir）统一转为基于 BASE 的绝对路径。
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(BASE, "config.json")

DEFAULTS = {
    "roots": [r"D:\MediaLibrary"],
    "category_filter": ["视频"],
    "db_path": os.path.join(BASE, "library.db"),
    "host": "127.0.0.1",
    "port": 8080,
    "memory_guard_bytes": 2147483648,
    "default_player_open": True,
    "default_user_id": 1,
    # 服务端预抽帧 & 日志（本次新增）
    "ffmpeg_path": "",        # 留空则用 imageio-ffmpeg 捆绑的 ffmpeg 二进制
    "frames_dir": "frames_cache",  # 抽帧小图缓存目录（相对项目根）
    "log_level": "INFO",      # DEBUG/INFO/WARNING/ERROR
    "log_dir": "logs",        # 运行日志目录（相对项目根）
}

_cache = None


def load():
    """读取配置（带进程级缓存）。返回 dict 副本，避免调用方误改缓存。"""
    global _cache
    if _cache is not None:
        return dict(_cache)
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    cfg["db_path"] = os.path.abspath(cfg["db_path"])
    # 相对路径目录统一转绝对路径（基于 BASE）
    for key in ("log_dir", "frames_dir"):
        val = cfg.get(key) or ""
        if val and not os.path.isabs(val):
            cfg[key] = os.path.join(BASE, val)
    _cache = cfg
    return dict(cfg)


def save(cfg):
    """写回 config.json 并刷新内存缓存。"""
    global _cache
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _cache = dict(cfg)
