"""用户评分均值结算（0~10 分制）。

两条路径，互为兜底：

1. **写入路径（即时、精确）**：用户评分变更后，立即只重算该作品的均值
   （单行两条相关子查询，代价极低）——保证界面与列表立刻拿到最新分数。
2. **空闲路径（批量、低优先）**：后台守护线程周期性扫描「评分有变动但均值未结算」
   的作品，**仅在没有任何长任务运行时**才批量重算 —— 即「资源不紧张时才统计平均值」。
   它同时负责自愈：即便某次写入路径失败（进程被杀、库锁），下次空闲也会补齐。

均值本身落在 ``media.rating_avg`` / ``rating_votes`` / ``rating_updated_at``，
由 ``db.recompute_rating_avg`` 统一执行 SQL；本模块只负责**何时**重算。
"""
import logging
import threading
import time

from .. import db
from . import jobs

log = logging.getLogger("vm.ratings")

# 评分量纲：0~10 分制（10 = 满分，0 = 最低分）
RATING_MAX = 10

# 空闲结算周期（秒）与单轮上限
SETTLE_INTERVAL_SEC = 25
SETTLE_BATCH = 500

_started = False
_start_lock = threading.Lock()


def recompute_now(con, media_ids) -> int:
    """写入路径：立即重算指定作品（调用方负责 commit）。"""
    return db.recompute_rating_avg(con, media_ids)


def stale_media_ids(con, limit: int = SETTLE_BATCH) -> list:
    """找出「用户评分有变动、但作品均值尚未结算」的作品 id。

    两类过期：
    - 该作品存在 ``updated_at`` 晚于 ``rating_updated_at`` 的用户评分；
    - 该作品均值非空，但已不存在任何用户评分（评分被全部清除）。
    """
    rows = con.execute(
        """SELECT DISTINCT media_id FROM (
             SELECT w.media_id AS media_id
               FROM watch_state w JOIN media m ON m.id = w.media_id
              WHERE w.personal_rating IS NOT NULL
                AND (m.rating_updated_at IS NULL OR w.updated_at > m.rating_updated_at)
             UNION
             SELECT m.id AS media_id FROM media m
              WHERE m.rating_avg IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM watch_state w2
                                 WHERE w2.media_id = m.id AND w2.personal_rating IS NOT NULL)
           ) LIMIT ?""",
        (int(limit),)).fetchall()
    return [r["media_id"] for r in rows]


def settle_once(con) -> int:
    """空闲路径单轮结算：无长任务在跑时重算过期作品。返回重算行数。"""
    job = jobs.current()
    if job is not None and job.is_running:
        return 0
    ids = stale_media_ids(con)
    if not ids:
        return 0
    n = db.recompute_rating_avg(con, ids)
    con.commit()
    if n:
        log.info("空闲结算用户评分均值：%d 部", n)
    return n


def _loop() -> None:
    while True:
        time.sleep(SETTLE_INTERVAL_SEC)
        try:
            con = db.connect()
            try:
                settle_once(con)
            finally:
                con.close()
        except Exception:  # noqa: BLE001 —— 后台线程绝不外抛，避免静默死掉
            log.exception("空闲结算线程异常（下个周期重试）")


def start_settler() -> None:
    """启动后台空闲结算线程（幂等，多次调用只启一个）。"""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="rating-settler", daemon=True).start()
    log.info("评分均值的空闲结算线程已启动（每 %ss 一次）", SETTLE_INTERVAL_SEC)
