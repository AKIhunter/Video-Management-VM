# `app/` — 后端应用根目录

后端全部代码，采用**单向分层**：`routers → services → core`，`services → db/config`，`core` 不依赖业务层。
依赖方向不可逆，否则会出现循环导入。

```
app/
├─ main.py            FastAPI 装配：lifespan（日志+建库）、/api/me、/api/poster、挂静态资源
├─ config.py          config.json 读写 + 默认值 + 进程级缓存
├─ db.py              SQLite 连接(WAL) / 建表 / 迁移 / settings / 分类字典同步
├─ authz.py           角色守卫（current_user / require_admin）
├─ cli.py             命令行入口：init / scan / tags-restore
├─ logging_setup.py   日志：控制台 + logs/app.log（按天滚动）
├─ core/              公共工具（无业务语义）
├─ services/          业务逻辑（17 个模块，见 services/README.md）
└─ routers/           HTTP 接口层（见 routers/README.md）
```

---

## 各文件职能

### `main.py` — 应用装配
- `lifespan()`：启动时初始化日志 + 调 `db.init()` 建表/迁移，结束时释放。
- `GET /api/me`：返回当前用户（前端据此决定是否显示「白屏管理」入口）。
- `GET /api/poster/{mid}`：封面出图；本地无素材时返回 `webui/assets/fallback.svg` 兜底，避免破图。
- 末尾 `app.mount("/", StaticFiles(...))`：**必须放在所有 API 路由之后**，否则会抢 `/api/*` 的匹配。
- 新增路由模块时：`from .routers import xxx` + `app.include_router(xxx.router)`。

### `config.py` — 配置
- `BASE`：项目根目录绝对路径（由本文件位置推导）。
- `CFG_PATH`：`<BASE>/config.json`。
- `DEFAULTS`：**所有配置项的默认值都在这里**。想加新配置键 → 在这里加一条，其它地方用 `cfg.get("key")` 即可。
- `load()`：读 `config.json` 并与 `DEFAULTS` 合并，**带进程级缓存**（改配置后需重启或调 `save()`）。
- `save(cfg)`：写回并清缓存。

> **改配置默认值 → `app/config.py :: DEFAULTS`**；改本机实际值 → 根目录 `config.json`。

### `db.py` — 数据库
- `SCHEMA` / `schema_sql()`：建表语句（**11 张表**，见下）。
- `connect()`：`sqlite3` 连接，开启 **WAL**、`row_factory=Row`、`busy_timeout`。
- `init()`：建表 + 轻量迁移。迁移方式是 `_columns()` 查现有列 + `_add_column()` 补列 —— **加字段就在 `init()` 里补一条迁移**。
- `_migrate_rating_scale()`：**一次性**把评分量纲从 0~5 换算到 0~10（`rating_norm` 与 `personal_rating` 均 ×2、封顶 10），
  以 `settings.rating_scale_v2` 为标记保证只跑一次；转换后按 `watch_state` 结算一次用户评分均值。
- `recompute_rating_avg(con, ids=None)`：按 `watch_state` 重算 `media.rating_avg` / `rating_votes` / `rating_updated_at`
  （业务上的调用时机在 `services/ratings.py`）。
- `sync_categories()`：把 `media.category` 里出现过、但 `categories` 字典缺的分类补齐（幂等），保证「扫描可选分类 / 作品管理 / 筛选下拉」三处数据一致。
- `get_setting()` / `set_setting()`：`settings` 键值表读写（如一键重启的随机 token）。
- `write_lock()`：写操作全局锁，避免并发写冲突。
- `get_db()`：FastAPI 依赖，按请求提供连接。

表清单：`users` `settings` `media` `media_fts` `tags` `media_tags` `watch_state` `scan_runs` `cover_reviews` `notifications` `categories`。

### `authz.py` — 权限
- `current_user()`：按配置的 `default_user_id` 从 **`users` 表**取当前用户（含 `role`），**不再写死 admin**；
  把该行改成 `role='user'` 即等价于「普通账号」，`require_admin` 会自动只读化对应功能。
  **要做多用户，只改这一个函数**（换成会话/token 解析），业务路由不用动。
- `require_admin()`：管理员守卫依赖。标签场景：新增接口用 `current_user`（谁都能加），删除接口用 `require_admin`（仅 admin）。

### `cli.py` — 命令行
- `python -m app.cli init` → `cmd_init`
- `python -m app.cli scan [...]` → `cmd_scan`（支持 `--dry-run` / `--scope` / `--path` / `--pending`）
- `python -m app.cli tags-restore --file X` → `cmd_tags_restore`
- 新增子命令：在 `main()` 的 `sub = parser.add_subparsers(...)` 下注册。

### `logging_setup.py` — 日志
- `setup_logging(cfg)`：`TimedRotatingFileHandler` 按天滚动到 `cfg["log_dir"]/app.log`，同时输出控制台。
- 改格式 / 保留天数 → 本文件；改级别 / 目录 → `config.json` 的 `log_level` / `log_dir`。

---

## 常见修改点速查

| 我想… | 改这里 |
|---|---|
| 加一个配置项 | `config.py :: DEFAULTS` + `config.example.json` + 文档 |
| 给某张表加字段 | `db.py` 的 `SCHEMA` + `init()` 里补一条 `_add_column` 迁移 |
| 加一个 HTTP 接口 | 对应 `routers/*.py`；逻辑写到 `services/`，路由层保持薄 |
| 加一段业务逻辑 | `services/` 下新建模块（一个模块一个职责），不要塞进 router |
| 改日志级别 / 目录 | `config.json` |
| 改端口 / 扫描根目录 | `config.json`（`port` / `roots`） |

---

## 相关文档
- 架构与数据流：[../docs/architecture.md](../docs/architecture.md)
- 部署与运行：[../docs/deployment.md](../docs/deployment.md)
