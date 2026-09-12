# `app/routers/` — HTTP 接口层

**保持薄**：只做「参数校验 → 调用 service → 组装响应」。任何业务逻辑都应落到 `services/`。
所有路由类都带 `prefix="/api"`，由 `app/main.py` 统一 `include_router`。

```
routers/
├─ media.py      首页：列表 / 详情 / 标签编辑 / 统计 / 推荐
├─ playback.py   播放 / 下载 / 本地打开
├─ userdata.py   个人化数据（状态 / 评分 / 收藏 / 备注）
└─ admin.py      白屏管理：运维 / 标签 IO / 封面 / 任务 / 重启（约 1500 行，按职责分段）
```

另外 `main.py` 直接挂了两个：`GET /api/me`、`GET /api/poster/{mid}`。

---

## `media.py` — 首页与查询

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/media` | 列表 + 筛选 + 分页。参数：`q` `category` `year_from/year_to` `month` `studio` `rating_min` `status` `favorite` `tag` `tags`(逗号分隔，OR) `sort` `page` `size` |
| GET | `/api/media/{mid}` | 详情（含标签列表、`meta`、收藏数） |
| GET | `/api/media/{mid}/related` | 旧版「同标签 + 随机补足」推荐（**保留兼容**） |
| GET | `/api/media/{mid}/recommend` | **播放页推荐轮播**（`per_page` `pages` `seed`）→ 见 [services/recommend.py](../services/README.md#11-recommendpy--播放页推荐权重要调就在这) |
| GET | `/api/tags` | 全部标签 + 使用数（供页眉标签气泡分栏） |
| GET | `/api/tagdict` | 题材词典的规范标签列表（供编辑联想） |
| POST / DELETE | `/api/media/{mid}/tags` | 加 / 删单个标签（会自动写入 `edited_fields`，防联网覆盖） |
| GET | `/api/stats` | 统计：总数 / 分类 / 年度 / 月份 / 状态 / 收藏 / 平均分 / 标签使用数 |

- 筛选拼装集中在 `_filters()`，**加筛选条件改这里**（注意同时改 `list_media` 的签名与前端）。
- `serialize(r)`：把行转成前端结构，`cover_mode` 从 `meta` 里解出来。
- `_mark_tagedited()`：人工改标签后打标记，让联网补全绕开。

## `playback.py` — 播放

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/play/{mid}` | Range 流式播放（走 `streaming.stream_file`） |
| GET | `/api/play/{mid}/meta` | 播放元信息：文件名 / 路径 / 内存守卫建议 |
| POST | `/api/open-local/{mid}` | 用系统默认播放器打开（受 `default_player_open` 约束） |
| GET | `/api/download/{mid}` | 下载原文件 |

## `userdata.py` — 个人化

| 方法 | 路径 | 说明 |
|---|---|---|
| PUT | `/api/media/{mid}/state` | 观看状态 / 个人评分 / 收藏 / 备注（`watch_state` 表） |

## `admin.py` — 白屏管理（按职责分段）

> 文件较大，顶部有分段注释。改动时**先定位分段**再动手。

**作品运维**
`POST /media/{mid}/edit`（编辑字段）· `GET /media/search`（模糊查 ID/名称/标签）· `GET /media/ids.txt`（导出索引 ID 文本）
`GET /media/list`（管理列表）· `POST /media/bulk`（批量：`move` / `delete` / `tag`，**delete 只删索引不删文件**）

**分类字典**
`GET/POST /categories` · `PUT/DELETE /categories/{cid}`（与 `db.sync_categories()` 共用同一份数据）

**标签**
`GET /tags` · `POST /tags` · `PUT/DELETE /tags/{tid}`
`GET /tags/export`（导出：每行 `<id>,<标签值>`，UTF-8 **带 BOM**）
`POST /tags/import`（导入：`mode=merge|overwrite`；覆盖导入会**快照 → 重建 → 重绑**，不脱绑）
`POST /tags/recover`（关联修复）

**联网补全 / 元数据**
`POST /completion` + `/completion/status` + `/completion/cancel`
`POST /metadata-backfill` + `/status` + `/cancel`
`POST /media/{mid}/metadata-refresh` · `POST /media/{mid}/metadata-apply`

**封面（四级流水线）**
`POST /cover`（本地匹配）· `POST /cover-dir`（指定目录补全）
`POST /cover-online`（联网搜索，生成待审定候选）· `/cover-online/status` · `/cover-online/cancel`
`POST /cover-video-frame`（标记用视频帧当封面）· `POST /cover-frame`（**ffmpeg 服务端预抽帧**）
`GET /cover-reviews` · `GET /cover-reviews/{rid}/preview/{idx}` · `POST /cover-reviews/{rid}/accept` · `POST /cover-reviews/batch-accept` · `POST /cover-reviews/batch-submit` · `POST /cover-reviews/{rid}/reject` · `POST /cover-reviews/{rid}/rescan`

**扫描 / 打标**
`POST /scan` + `/scan/status` + `/scan/cancel`
`POST /tags-backfill` + `/status` + `/cancel`

**任务 / 通知 / 系统**
`GET /task`（当前任务全量状态）· `GET /notifications*`（列表 / 未读数 / 已读 / 详情）
`GET /pending` · `GET /config`（读当前配置）· `GET /restart-token` · `POST /restart`（`__RESTART__` + 随机 token 双重确认）

---

## 常见修改点速查

| 我想… | 改这里 |
|---|---|
| 加筛选条件 | `media.py :: _filters()` + `list_media()` 签名 + 前端筛选栏 |
| 加管理侧接口 | `admin.py`，按分段插入；复杂逻辑抽到 `services/` |
| 改导出格式 | `admin.py` 的 `tags/export` |
| 改一键重启行为 | `admin.py` 的 `restart()` + `RESTART_CONFIRM` |
| 加通知类型 | `admin.py :: _push_notification` 调用处 + 前端 `logs.html` |
