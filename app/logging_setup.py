"""应用运行日志：Python logging 落盘到 logs/ 目录（按天滚动）+ 控制台。

- setup_logging(cfg)：幂等配置 root logger（重复调用不叠加 handler）。
- 日志文件 logs/app.log，按天滚动，保留 7 份；级别由 config.json 的 log_level 控制。
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

from .config import BASE


def setup_logging(cfg):
    """配置根日志器（幂等）。cfg 为 config.load() 的返回。"""
    log_dir = cfg.get("log_dir") or os.path.join(BASE, "logs")
    if not os.path.isabs(log_dir):
        log_dir = os.path.join(BASE, log_dir)
    level = getattr(logging, str(cfg.get("log_level") or "INFO").upper(), logging.INFO)

    root = logging.getLogger()
    if root.handlers:            # 已配置过（幂等，避免重复 handler）
        return root
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    os.makedirs(log_dir, exist_ok=True)
    fh = TimedRotatingFileHandler(
        os.path.join(log_dir, "app.log"), when="midnight", backupCount=7, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    return root
