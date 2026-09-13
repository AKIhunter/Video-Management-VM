-- WAL 与 busy_timeout 由 db.py 在每个连接上设置（此处保留参考）
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- 用户（v1 seed id=1 默认 admin；管理中心可增删改查）
CREATE TABLE IF NOT EXISTS users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT UNIQUE,
  role       TEXT NOT NULL DEFAULT 'user',   -- 'admin' / 'user'
  points     INTEGER NOT NULL DEFAULT 0,     -- 成长值（签到累计），决定等级
  checkin_days    INTEGER NOT NULL DEFAULT 0, -- 累计签到天数
  checkin_streak  INTEGER NOT NULL DEFAULT 0, -- 连续签到天数（断签清零）
  last_checkin    TEXT,                       -- 最近签到日期（YYYY-MM-DD，「每日一次」判断用）
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 站点级设置（白屏管理读取/写入，如重启校验 token 等）
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- 媒体主表（通用模型，category 区分分类）
CREATE TABLE IF NOT EXISTS media (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  category      TEXT NOT NULL DEFAULT '视频',
  title         TEXT NOT NULL,
  title_jp      TEXT,
  studio        TEXT,
  publish_date  TEXT,
  year          INTEGER,
  subtitle      INTEGER DEFAULT 0,
  file_path     TEXT NOT NULL UNIQUE,
  file_size     INTEGER,
  file_ext      TEXT,
  duration_sec  INTEGER,
  poster_path   TEXT,
  rating_norm   REAL,                        -- 历史/简评评分，量纲 0~10（旧 0~5 已 ×2 换算）
  rating_raw    TEXT,
  rating_source TEXT,
  rating_avg    REAL,                        -- 用户评分均值 0~10（由 watch_state 统计；无人评分时 NULL）
  rating_votes  INTEGER DEFAULT 0,           -- 参与均值的用户数
  rating_updated_at TEXT,                    -- 均值最近结算时间（空闲重算据此判断过期）
  synopsis      TEXT,
  edited_fields TEXT DEFAULT '[]',
  meta          TEXT,
  scan_hash     TEXT,
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  updated_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_media_year   ON media(year, rating_norm);
CREATE INDEX IF NOT EXISTS idx_media_studio ON media(studio);
CREATE INDEX IF NOT EXISTS idx_media_cat    ON media(category, publish_date);
CREATE INDEX IF NOT EXISTS idx_media_rate   ON media(rating_norm DESC);
CREATE INDEX IF NOT EXISTS idx_media_title  ON media(title);

-- 全文检索
CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5(
  title, title_jp, studio,
  content='media', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS media_ai AFTER INSERT ON media BEGIN
  INSERT INTO media_fts(rowid, title, title_jp, studio)
  VALUES (new.id, new.title, coalesce(new.title_jp,''), coalesce(new.studio,''));
END;
CREATE TRIGGER IF NOT EXISTS media_ad AFTER DELETE ON media BEGIN
  INSERT INTO media_fts(media_fts, rowid, title, title_jp, studio)
  VALUES ('delete', old.id, old.title, coalesce(old.title_jp,''), coalesce(old.studio,''));
END;
CREATE TRIGGER IF NOT EXISTS media_au AFTER UPDATE ON media BEGIN
  INSERT INTO media_fts(media_fts, rowid, title, title_jp, studio)
  VALUES ('delete', old.id, old.title, coalesce(old.title_jp,''), coalesce(old.studio,''));
  INSERT INTO media_fts(rowid, title, title_jp, studio)
  VALUES (new.id, new.title, coalesce(new.title_jp,''), coalesce(new.studio,''));
END;

-- 标签
CREATE TABLE IF NOT EXISTS tags (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS media_tags (
  media_id INTEGER,
  tag_id   INTEGER,
  PRIMARY KEY(media_id, tag_id)
);

-- 个人化：观看状态 / 评分 / 收藏
CREATE TABLE IF NOT EXISTS watch_state (
  media_id        INTEGER NOT NULL,
  user_id         INTEGER NOT NULL DEFAULT 1,
  status          TEXT DEFAULT '未看',
  personal_rating REAL,
  favorite        INTEGER DEFAULT 0,
  note            TEXT,
  updated_at      TEXT DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (media_id, user_id)
);

-- 扫描运行日志
CREATE TABLE IF NOT EXISTS scan_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT,
  finished_at TEXT,
  scanned     INTEGER,
  added       INTEGER,
  updated     INTEGER,
  removed     INTEGER,
  note        TEXT
);

-- 封面人工审定：联网搜索未达自动采用阈值（低置信度）或未找到来源的作品，
-- 保存候选图 + 来源帖子，供管理员逐条确认后写回 poster_path。
CREATE TABLE IF NOT EXISTS cover_reviews (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  media_id      INTEGER NOT NULL,
  candidates    TEXT NOT NULL DEFAULT '[]',  -- JSON: 候选图 URL 数组（可为空）
  post_url      TEXT,                        -- 来源帖子链接
  title_matched TEXT,                        -- 命中的帖子标题
  confidence    REAL DEFAULT 0,              -- 帖子标题与作品关键词的相似度
  error         TEXT,                        -- 未找到原因（无源时记录）
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending / accepted / rejected
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  updated_at    TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(media_id, status)
);
CREATE INDEX IF NOT EXISTS idx_cover_reviews_status ON cover_reviews(status, media_id);

-- 消息通知：服务告警 + 任务完成/失败通知（气泡图标展示，详情页看完整日志）
CREATE TABLE IF NOT EXISTS notifications (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  type       TEXT NOT NULL DEFAULT 'info',   -- task_done / task_canceled / task_error / info
  title      TEXT NOT NULL,                  -- 一句话标题（气泡列表展示）
  body       TEXT,                           -- 完整日志（详情页展示）
  is_read    INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read, id);

-- 分类字典：扫描的可选分类 / 作品管理 / 页面「全部分类」筛选三处共用（同一份数据，互相耦合）
CREATE TABLE IF NOT EXISTS categories (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL UNIQUE,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);