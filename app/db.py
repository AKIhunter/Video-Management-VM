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
    _add_column(con, "users", "role", "TEXT NOT NULL DEFAULT 'user'")
    # 保证 id=1 为默认 admin
    con.execute(
        "INSERT INTO users(id,name,role) VALUES(1,'admin','admin') "
        "ON CONFLICT(id) DO UPDATE SET name='admin', role='admin'"
    )
    sync_categories(con)
    con.commit()
    if close:
        con.close()


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