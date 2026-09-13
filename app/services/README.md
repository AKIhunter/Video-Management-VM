# `app/services/` — 业务逻辑层

**所有业务逻辑都在这里**。一个模块一个职责，`routers/` 只做参数校验后调这里的函数。
依赖规则：可以 import `core/`、`db`、`config`，**不允许** import `routers/`。

```
services/
├─ parser.py             文件名 / 海报 / 简评评分解析
├─ scanner.py            扫描产出「待导入清单」（全量 / 定点、幂等、可取消、dry_run 暂存）
├─ scan_stage.py         扫描暂存区：待导入清单的内存存取（人工点「导入」才真正写库）
├─ backfill.py           按目录结构回填年份 / 日期 / 制作组
├─ tagging.py            本机简评文本打标（离线，走题材词典）
├─ kinks.py              题材词典与标签规范化（TAG_LIMIT）
├─ metadata_provider.py  联网元数据解析器（本地 / 百度 / media-db 级联）
├─ completion.py         联网补全工作器（尊守 edited_fields，候选制）
├─ covers.py             本地封面匹配 + 指定目录补全（旧版视频帧标记已移除，仅保留遗留值兼容）
├─ online_covers.py      联网封面搜索 / 下载 / 人工审定候选
├─ frames.py             ffmpeg 服务端预抽帧（落盘小图写回 poster_path）
├─ recommend.py          播放页推荐轮播：多因子加权打分 + 分页编排
├─ ratings.py            用户评分均值结算（写入即时重算 + 空闲批量重算）
├─ levels.py             账号等级 / 每日签到规则 + 等级权益预留门槛（FEATURE_LEVELS）
├─ tag_restore.py        按导出 JSON 还原 media_tags 关联（只重建 id↔id）
├─ streaming.py          流式播放（Range 直传 + 不友好格式 ffmpeg 实时转码）+ 2GB 守卫 + 本地打开
└─ jobs.py               长任务互斥运行器（进度 / 取消）
```

---

## 模块详解

### 1. `parser.py` — 解析（无副作用，最好测）
- `parse_video_filename(name)`：解析三时代文件名规则 → 标题 / 制作组 / 日期 / 副标题标记。
- `normalize_title(s)`：标题归一（去冗余后缀 / 全半角 / 空白）。
- `derive_year_from_path(path)`：从目录结构派生年份 / 月份。
- `find_poster(video_path)`：封面查找（同名 → 上级目录 → 标题模糊）。
- `normalize_score(text)` / `parse_jianping(text)`：简评评分换算（★★☆、4 星半、箭头、双档各种写法）。
  **库内量纲 = 0~10**：原文星级（0~5）在 `normalize_score` 里 ×2 并封顶 10（常量 `RATING_MAX` / `_STAR_SCALE`）。
  内部 `_normalize_score_raw()` 返回未换算的 0~5，仅供拆分逻辑使用。
- **改文件名规则 / 评分换算规则 → 本文件**，且务必补 `tests/test_parser.py` 用例。
- 视频扩展名白名单 `VIDEO_EXTS`、图片 `IMAGE_EXTS`、字幕 `SUBTITLE_EXTS` 都在文件顶部。

### 2. `scanner.py` — 扫描（**两段式：扫描 → 人工确认 → 导入**）
- `do_scan(cfg, dry_run, progress, scope, path, stop, category_override, stage)`：主入口。
  `scope="full"` 扫全部 `roots`；`scope="path"` 只处理指定目录。
  - **生产路径一律 `dry_run=True` + `stage=list`**：把「新增 / 有变化」的候选收集进 `stage`，**不写库**；
    界面展示清单后由人工点「导入到索引」才入库（见 `import_staged`）。
- 幂等：以 `path + size + mtime` 生成指纹（`_hash`），内容没变就跳过（也不进清单）。
- 按目录分块（`_iter_chunks`）以便中途取消（协作式，检查 `stop()`）。
- `_category_for(path)` 决定作品归到哪个分类；`_match_ratings(con, cfg)` 回填简评评分。
- `source_group(path)`：**来源组**（写入 studio，替换旧「制作组」语义）—— `上级文件夹/本级文件夹`，
  上级为数据盘根（MediaLibrary）时仅记本级；扫描产出与导入落库时都会自动生成。
- **定点扫描不会删除范围之外的记录**。
- `import_staged(items, category, progress, stop)`：把暂存清单真正写库。
  分类用**人工指定**的 `category`（路由层防呆拒绝「自动（按目录名）」）；每条导入前**二次校验**
  文件仍在且指纹一致，否则跳过；**不做「移除已消失文件」**（删除与导入解耦，清理用作品管理）。
- 改扫描范围策略 / 指纹算法 / 分块大小 → 本文件。

### 2.1 `scan_stage.py` — 扫描暂存区
- 待导入清单的**进程内存**存取（**重启即清空**，避免遗留过期路径）：
  `set_stage` / `summary` / `items` / `view` / `clear`。
- `view()` 只外露展示字段（action / 标题 / 分类 / 路径 / 有无封面），不外露 `scan_hash`、`meta`。
- 改扫描范围策略 / 指纹算法 / 分块大小 → 本文件。

### 3. `backfill.py` — 目录派生回填
`run_metadata_backfill(job)`：利用「文件夹已按年份 / 月份分类」这一事实，把缺失的 `year / publish_date / studio` 从路径补出来（不联网）。

### 4. `kinks.py` — 标签词典
- `KINK_ENTRIES`：`(正则, 规范标签)` 列表，**所有词条都在这里**。
- `TAG_LIMIT = 10`：每个作品的标签数上限（业务上限，导入 / 批量打标都以此为准）。
- `pick_tags(texts)` / `filter_kink(tags)`：从文本或候选标签里挑规范标签。
- **加词条 / 改上限 → 本文件**（改动后建议跑 `tests/test_kinks.py`）。

### 5. `tagging.py` — 离线打标
`run_tag_backfill(job)`：读磁盘「简评」txt，按 `kinks` 词典提标签写库（不联网）。辅助函数 `collect_entries` / `tags_for_title`。

### 6. `metadata_provider.py` — 联网元数据
- 统一候选协议：`resolve(media_row) -> dict`。
- 解析器：`LocalResolver` → `BaiduResolver` → `MediaDbResolver`，由 `CascadeResolver` 级联调用。
- `validate_candidate()`：清洗候选，约束 `MAX_SYNOPSIS`(简介字数) / `MAX_TAGS`(候选 tag 数) / `MAX_TAG_LEN`(单 tag 长度) / 发布日期格式。
- `UA`：请求头 User-Agent。站点结构变了 → 改 `extract_media_db_results` / `extract_media-db_post` / `extract_baidu_synopsis`。
- **改简介 / 标签长度上限 → 文件顶部常量**（注意：业务上限另见 `kinks.TAG_LIMIT`）。

### 7. `completion.py` — 联网补全工作器
`run_completion(job, source, mid, ids, resolver)`：对缺信息的作品回填。
- 分批提交、可取消；`_needs_completion()` 判定哪些字段缺。
- 通过 `edited()` 检查，**跳过人工编辑过的字段**。
- `_stamp_meta()` 把补全痕迹写进 `media.meta`。

### 8. `covers.py` — 本地封面
- `CoverIndex` + `build_cover_index(roots)`：扫描磁盘上所有封面目录（识别规则 `is_cover_dir` / `is_cover_file`），建索引。
- `resolve(video_path, index, threshold, local_first)`：按相似度匹配（`_ratio` / `_lcs_len` / `_pick`）。
- `run_cover_backfill(job)`：批量本地匹配。
- `run_cover_dir_backfill(cfg, root, threshold, ...)`：**指定目录补全**（只在这个目录里找图）。
- ~~`set_video_frame_cover`~~：**已移除**（旧版「视频帧封面标记」由服务端抽帧替代）；遗留的 `meta.cover_mode=video_frame` 值仍可被序列化/前端渲染，抽帧成功后自动清掉。
- **改匹配阈值 / 封面目录识别规则 → 本文件**。

### 9. `online_covers.py` — 联网封面
每部缺封面作品：搜索 → 过滤 → 建候选 → 入库待审定 → 人工点「采用」才下载落盘。
- 常量：`MIN_WH`(最小边长) `MAX_WH_RATIO`(长宽比上限) `MIN_CONF`(标题相似度下限) `KW_MAX`(关键词长度) `CACHE_DIR`(下载缓存目录)。
- `search_cover()` 搜候选；`download_cover()` 下载并写 `poster_path`；`save_review()` 落 `cover_reviews` 待审定记录；`run_online_cover_backfill(job)` 批量入口。

### 10. `frames.py` — 服务端抽帧（性能关键路径）
- `ffmpeg_exe(cfg)`：优先 `config.ffmpeg_path`，否则用 `imageio-ffmpeg` 捆绑版。
- `frame_dir(cfg)`：抽帧输出目录（默认 `frames_cache/`）。
- `video_duration()` / `extract_frame()` / `is_black_image()` / `pick_bright_frame()`：抽帧并挑「不黑」的一帧。
- `run_frame_backfill(job, ids)`：批量抽帧，落盘 `frames_cache/{id}.jpg` 并写回 `poster_path`。
  `ids` = **A 区解析出的索引ID**（路由层强制必填，只处理范围内缺封面作品）；
  进度在**处理完一条后**回报 `done = i+1`（此前用 0 起始的 i，进度条永远停在 (total-1)/total，看起来卡在一半）。
  旧版 `cover_mode=video_frame` 标记不再是前置条件，抽帧成功后自动清掉该标记。
- 常量 `FRAME_WIDTH = 512`（抽帧宽度，够网格 / 详情用）—— **想更清晰改这里**。

### 11. `recommend.py` — 播放页推荐（**权重要调就在这**）
- 8 因子线性加权，常量都在文件顶部：`W_TAG` / `W_STUDIO` / `W_CATEGORY` / `W_YEAR` / `W_RATING` / `W_STATUS` / `W_FRESH` / `JITTER`。
- **候选范围**：`SAME_CATEGORY_ONLY=True`（默认）→ 只在**同一分类**内推荐（真人只推真人、视频只推视频，
  新增分类免改代码）；同分类无人时兜底放开（响应 `scope=fallback_all`）。`_candidates(category=)` 控制该条件。
- 多样性：`MAX_SAME_STUDIO`(每页同制作组上限) / `STUDIO_PENALTY`(重复惩罚) / `POOL_FACTOR`(参与编排的候选倍数)。
- `_profile()` 取当前作品画像 → `_candidates()` 一次拉候选并算共享标签数 → `_score()` 打分 → `_arrange()` 逐页贪心编排
  → `_serialize()` 出前端结构 → `recommend()` 返回分页。
- ⚠️ 评分必须按「全库 `MIN/MAX` 区间」归一（库内评分量纲是 **0~10**，不是 0~1）；
  候选与画像取的是**有效评分** `COALESCE(m.rating_avg, m.rating_norm)`。
- **打分严格只读 id / 计数 / 数值**；唯一读标签文本的地方是 `_shared_tag_names()`：
  把「共享标签 id」映射成 `shared_tag_names`（上限 `SHARED_TAG_LIMIT=6`）**仅供卡片展示**，不参与计算。
- 卡片展示字段：`shared_tag_names`（具体标签名，替代「N 个相同标签」模糊文案）+ `publish_date`（卡片文字行显示发布时间）；
  制作组**不出现在卡片上**（`W_STUDIO` 仍参与打分与多样性约束）。
- 权重表详见 [../../docs/architecture.md](../../docs/architecture.md#推荐打分因子appservicesrecommendpy)。

### 12. `ratings.py` — 用户评分均值（0~10）
- 均值落在 `media.rating_avg`（均值）/ `rating_votes`（人数）/ `rating_updated_at`（结算时间）。
- `recompute_now(con, ids)`：**写入路径** —— 评分变更后立即只重算该作品（单行，代价极低），调用方负责 commit。
- `stale_media_ids(con)` / `settle_once(con)`：**空闲路径** —— 找出「评分有变动但均值未结算」的作品批量重算，
  **仅在 `jobs.current()` 无运行任务时执行**（即「资源不紧张时才统计平均值」），同时自愈漏算的行。
- `start_settler()`：启动后台守护线程（`main.py` 的 lifespan 里调用一次，幂等）。
- 常量：`RATING_MAX=10`（量纲上限，路由层复用）、`SETTLE_INTERVAL_SEC=25`、`SETTLE_BATCH=500`。

### 12.1 `levels.py` — 账号等级 / 每日签到（个人中心）
- 规则：签到**每天一次**，基础 `POINTS_PER_CHECKIN=10` 成长值，连续每满 `STREAK_BONUS_EVERY=7` 天额外
  `+STREAK_BONUS=20`；等级 `level = min(MAX_LEVEL=30, 1 + points // POINTS_PER_LEVEL=100)`。
- 纯函数：`level_of` / `next_level_need` / `reward` / `next_streak`（断签清零）。
- **预留门槛**：`FEATURE_LEVELS`（功能 → 所需等级）+ `can_use(level, feature)`。
  未来要按等级限制某功能：在映射表加一行、在路由里调一次 `can_use` 即可。当前无调用点。

### 13. `tag_restore.py` — 关联还原
`parse_export()` / `restore()` / `restore_from_file()`：按外部导出文件把 `media_tags` 的 **id↔id 关联**重建回去。
**只重建关联，不写入也不保留标签文本**。

### 14. `streaming.py` — 播放
- `stream_file(path, request, chunk)`：HTTP Range 流式响应（分块 `CHUNK = 1MB`）。
- `needs_transcode(path)` / `stream_transcode(path, request, cfg)`：浏览器不便播放的格式
  （`TRANSCODE_EXTS`：avi/wmv/flv/rmvb 等）→ ffmpeg **实时转码**为分片 mp4（H.264+AAC、720p、veryfast），
  边转边流、断开即杀子进程；转码流**不支持拖动进度条**。mp4/mkv/webm 等（`NATIVE_EXTS`）走原生直传。
- `guard_advice(media_row, cfg)`：**仅超过 2GB**（`memory_guard_bytes` 默认 2147483648）建议本地播放。
- `open_local(path)`：调系统默认播放器（受 `default_player_open` 开关约束）。

### 15. `jobs.py` — 长任务运行器
- `class Job`：`tick()` 报进度、`stop()` 请求取消、状态查询。
- `run_exclusive(job)`：**全局互斥单槽** —— 同一时刻只允许一个长任务。
- `current()`：当前任务（`/api/admin/task` 用它汇报）。
- 新增长任务：构造 `Job` → 传 `run_exclusive` → 在 worker 里分块并检查 `job.stop`。

---

## 常见修改点速查

| 我想… | 改这里 |
|---|---|
| 改文件名解析规则 | `parser.py` |
| 改扫描范围 / 增量指纹 | `scanner.py` |
| 改标签词条 / 每作品标签数上限 | `kinks.py`（`KINK_ENTRIES` / `TAG_LIMIT`） |
| 改简介 / 标签长度约束 | `metadata_provider.py` 顶部常量 |
| 改封面匹配阈值 | `covers.py :: resolve(threshold=)` 调用处 / 默认值 |
| 改抽帧尺寸 | `frames.py :: FRAME_WIDTH` |
| 改评分量纲 / 简评↔分数换算 | `parser.py :: RATING_MAX` / `_STAR_SCALE` + `ratings.py :: RATING_MAX` |
| 改评分均值结算时机 | `ratings.py :: SETTLE_INTERVAL_SEC` / `SETTLE_BATCH`（写入路径在 `userdata.py`） |
| **调推荐权重 / 翻页数** | `recommend.py` 顶部常量 + `webui/player.html` 的 `PER_PAGE` / `MAX_PAGES` |
| 改流式分块大小 | `streaming.py :: CHUNK` |
| 加一个新长任务 | `jobs.py` 的 `Job` + `routers/admin.py` 注册接口 |
