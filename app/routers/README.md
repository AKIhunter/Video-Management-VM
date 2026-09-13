# `app/routers/` — HTTP 接口层

**保持薄**：只做「参数校验 → 调用 service → 组装响应」。任何业务逻辑都应落到 `services/`。
所有路由类都带 `prefix="/api"`，由 `app/main.py` 统一 `include_router`。

```
routers/
├─ media.py      首页：列表 / 详情 / 标签编辑 / 统计 / 推荐
├─ playback.py   播放 / 下载 / 本地打开
├─ userdata.py   个人化数据（状态 / 评分 / 收藏 / 备注）
├─ account.py    个人中心：/api/me（含等级 / 签到）/ me/checkin / me/favorites
└─ admin.py      白屏管理：运维 / 账号 CRUD / 标签 IO / 封面 / 任务 / 重启（按职责分段）
```

另外 `main.py` 直接挂了 `GET /api/poster/{mid}`；`GET /api/me` 由 `account.py` 提供
（含角色 / 等级 / 签到状态，前端据此决定「白屏管理」入口与个人中心展示）。

> `/api/me` 返回当前账号 `{id, name, role}`——角色取自 `users` 表（不再写死 admin）。
> 前端据此决定：是否显示「白屏管理」入口、标签是否渲染删除按钮。

---

## `media.py` — 首页与查询

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/media` | 列表 + 筛选 + 分页。参数：`q` `category` `year_from/year_to` `month` `studio` `rating_min` `status` `favorite` `tag` `tags`(逗号分隔，OR) `sort` `page` `size` |
| GET | `/api/media/{mid}` | 详情（含标签列表、`meta`、收藏数） |
| GET | `/api/media/{mid}/related` | 旧版「同标签 + 随机补足」推荐（**保留兼容**） |
| GET | `/api/media/{mid}/recommend` | **播放页推荐轮播**（`per_page` `pages` `seed`）→ 见 [services/recommend.py](../services/README.md#11-recommendpy--播放页推荐权重要调就在这)。卡片展示字段：`publish_date`、`shared_tag_names`（共享标签的具体值，替代模糊文案） |
| GET | `/api/tags` | 全部标签 + 使用数（供页眉标签气泡分栏） |
| GET | `/api/kinks` | 题材词典的规范标签列表（供编辑联想） |
| POST / DELETE | `/api/media/{mid}/tags` | 加 / 删单个标签（会自动写入 `edited_fields`，防联网覆盖）。**POST 任何账号可调；DELETE 仅 admin**（普通账号只增不删） |
| GET | `/api/stats` | 统计：总数 / 分类 / 年度 / 月份 / 状态 / 收藏 / 平均分 / 标签使用数 |

- 筛选拼装集中在 `_filters()`，**加筛选条件改这里**（注意同时改 `list_media` 的签名与前端）。
  `rating_min` 与「按评分排序」用的是**有效评分** `EFF_RATING = COALESCE(m.rating_avg, m.rating_norm)`。
- `serialize(r)`：把行转成前端结构，`cover_mode` 从 `meta` 里解出来；
  另补 `rating_votes` 与 `rating_display`（有用户评分取均值，否则回退 `rating_norm`）。
- `_mark_tagedited()`：人工改标签后打标记，让联网补全绕开。

## `playback.py` — 播放

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/play/{mid}` | 流式播放。mp4/mkv/webm 等原生格式走 Range 直传；**avi/wmv/rmvb 等自动改走 ffmpeg 实时转码**（分片 mp4，进度条不可拖动） |
| GET | `/api/play/{mid}/meta` | 播放元信息：文件名 / 路径 / 守卫建议（**仅 >2GB** 建议本地播放）/ `transcode` 是否转码 |
| GET | `/api/play/{mid}/related` | **关联作品**：标题高度相似 + 同目录加权，供播放页右栏快速切换与自动连播；附带各作品**原有标签**（`tags`） |
| POST | `/api/open-local/{mid}` | 用系统默认播放器打开（受 `default_player_open` 约束） |
| GET | `/api/download/{mid}` | 下载原文件 |

## `userdata.py` — 个人化

| 方法 | 路径 | 说明 |
|---|---|---|
| PUT | `/api/media/{mid}/state` | 观看状态 / 个人评分 / 收藏 / 备注（`watch_state` 表） |
- **评分量纲 0~10**（`0` 是合法最低分；显式传 `null` = 清除评分）。
- 每个用户对每个作品**只有一条记录**（主键 `(media_id, user_id)`），重复评分直接覆盖（UPSERT）。
- 写完评分后**立即重算该作品的用户评分均值**（`services/ratings.recompute_now`），响应里带回
  `{avg, votes, display}` 供前端就地刷新卡片角标。

## `account.py` — 个人中心

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/me` | 当前账号（角色 / 等级 / 成长值 / 签到状态 / 等级权益预留 `perks`） |
| POST | `/api/me/checkin` | **每日签到**（每天一次；+成长值，连续每满 7 天额外 +20），等级随成长值提升 |
| GET | `/api/me/favorites` | 个人收藏夹（当前账号收藏的作品，不含文件路径） |

- 等级规则见 [services/levels.py](../services/README.md#121-levelspy--账号等级--每日签到个人中心)；
  按等级做内容限制时，在 `FEATURE_LEVELS` 声明并在路由里调 `levels.can_use(...)`（当前预留、无调用点）。

## `admin.py` — 白屏管理（按职责分段）

> 文件较大，顶部有分段注释。改动时**先定位分段**再动手。

**账号管理（管理中心）**
`GET /users`（列表，含等级/签到概况与 `is_default` 标记）· `POST /users`（新增，role ∈ admin/user）
`PUT /users/{uid}`（改名 / 改角色；**默认登录账号不可降级**）· `DELETE /users/{uid}`（**默认登录账号不可删除**；同时清掉该账号的观看状态）

**作品运维**
`POST /media/{mid}/edit`（编辑字段，`rating_norm` 落库前按 0~10 夹取）· `GET /media/search`（模糊查 ID/名称/标签）· `GET /media/ids.txt`（导出索引 ID 文本）
`GET /media/list`（管理列表）· `GET /media/duplicates`（**重复检测**：按分类返回标题相似分组，含 `ignored_groups`）
`POST /media/duplicates/ignore`（**本组忽略**：按组内归一化标题记录，`undo` 撤销 / `clear` 清空）
`POST /media/bulk`（批量：`move` / `delete` / `tag`，**delete 只删索引不删文件**）
`POST /media/delete-files`（**删除作品：索引 + 磁盘真实文件**，破坏性；视频必删、服务端缓存封面一并删、素材目录海报保留并列出；前端二次确认）

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
`POST /cover-frame`（**ffmpeg 服务端预抽帧**；`ids` 必填 = A 区解析出的索引ID，只处理范围内缺封面作品）。旧版 `POST /cover-video-frame`（视频帧标记）已移除。
`GET /cover-reviews` · `GET /cover-reviews/{rid}/preview/{idx}` · `POST /cover-reviews/{rid}/accept` · `POST /cover-reviews/batch-accept` · `POST /cover-reviews/batch-submit` · `POST /cover-reviews/{rid}/reject` · `POST /cover-reviews/{rid}/rescan`

**扫描（两段式）/ 打标**
`POST /scan`（**只产出待导入清单**，不写库）· `GET /scan/staged`（清单，分页）· `POST /scan/import`（**人工导入**；防呆：必须传具体 `category`，拒绝「自动（按目录名）」）· `POST /scan/staged/clear`（放弃清单）
`GET /scan/status` + `POST /scan/cancel`（导入也走 `scan` 任务通道，`meta.phase=import`）
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
