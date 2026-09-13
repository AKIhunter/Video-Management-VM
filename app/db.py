import os
import sqlite3
import threading

from . import config

_schema_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
_write_lock = threading.Lock()


def schema_sql() -> str:
    with open(_schema_py, encoding="utf-8") as f:
        return f.read()


def connect() -> sqlite3.Connection:
    """Create a new SQLite connection with WAL + busy_timeout."""
    cfg = config.load()
    dbp = cfg["db_path"]
    os.makedirs(os.path.dirname(dbp), exist_ok=True)
    con = sqlite3.connect(dbp, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _columns(con, table: str) -> set:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _add_column(con, table: str, column: str, decl: str) -> None:
    if column not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init(con: sqlite3.Connection | None = None) -> None:
    close = con is None
    if con is None:
        con = connect()
    con.executescript(schema_sql())
    # 旧库迁移：仅为缺失列补列（新库 CREATE TABLE 已含，重跑幂等）
    _add_column(con, "media", "synopsis", "TEXT")
    _add_column(con, "media", "edited_fields", "TEXT DEFAULT '[]'")
    _add_column(con, "media", "rating_avg", "REAL")
    _add_column(con, "media", "rating_votes", "INTEGER DEFAULT 0")
    _add_column(con, "media", "rating_updated_at", "TEXT")
    _add_column(con, "users", "role", "TEXT NOT NULL DEFAULT 'user'")
    _add_column(con, "users", "points", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "users", "checkin_days", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "users", "checkin_streak", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "users", "last_checkin", "TEXT")
    # 保证 id=1 为默认 admin
    con.execute(
        "INSERT INTO users(id,name,role) VALUES(1,'admin','admin') "
        "ON CONFLICT(id) DO UPDATE SET name='admin', role='admin'"
    )
    sync_categories(con)
    _migrate_rating_scale(con)
    con.commit()
    if close:
        con.close()


_RATING_SCALE_FLAG = "rating_scale_v2"


def _migrate_rating_scale(con: sqlite3.Connection) -> None:
    """一次性把评分量纲从 0~5 换算到 0~10（new = old × 2，封顶 10）。

    迁移对象：``media.rating_norm``（历史/简评评分）与 ``watch_state.personal_rating``（用户评分）。
    迁移后立即按 watch_state 结算一次用户评分均值。用 settings 标记保证只跑一次。
    """
    if get_setting(con, _RATING_SCALE_FLAG) == "1":
        return
    con.execute(
        "UPDATE media SET rating_norm = MIN(rating_norm * 2, 10) "
        "WHERE rating_norm IS NOT NULL")
    con.execute(
        "UPDATE watch_state SET personal_rating = MIN(personal_rating * 2, 10) "
        "WHERE personal_rating IS NOT NULL")
    recompute_rating_avg(con)
    set_setting(con, _RATING_SCALE_FLAG, "1")


def recompute_rating_avg(con: sqlite3.Connection, media_ids=None) -> int:
    """按 ``watch_state`` 重算 ``media`` 的用户评分均值 / 人数 / 结算时间，返回受影响行数。

    ``media_ids=None`` 表示全库重算；否则只算给定 id。无人评分时 ``rating_avg`` 置空
    （读取侧用 ``COALESCE(rating_avg, rating_norm)`` 回退到历史分）。
    """
    base = (
        "UPDATE media SET "
        "rating_avg = (SELECT ROUND(AVG(w.personal_rating), 2) FROM watch_state w "
        "              WHERE w.media_id = media.id AND w.personal_rating IS NOT NULL), "
        "rating_votes = (SELECT COUNT(*) FROM watch_state w "
        "               WHERE w.media_id = media.id AND w.personal_rating IS NOT NULL), "
        "rating_updated_at = datetime('now','localtime')"
    )
    if media_ids is None:
        return con.execute(base).rowcount
    ids = [int(i) for i in media_ids]
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    return con.execute(base + f" WHERE id IN ({ph})", ids).rowcount


def sync_categories(con: sqlite3.Connection) -> None:
    """把 media 中已出现的分类补进分类字典（幂等，供扫描/作品管理/页面筛选共用）。"""
    con.execute(
        "INSERT OR IGNORE INTO categories(name) "
        "SELECT DISTINCT category FROM media WHERE category IS NOT NULL AND category != ''")


def get_setting(con, key: str, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


def write_lock():
    return _write_lock


def get_db():
    """FastAPI dependency: one connection per request."""
    con = connect()
    try:
        yield con
    finally:
        con.close()