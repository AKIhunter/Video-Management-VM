# `app/services/` — 业务逻辑层

**所有业务逻辑都在这里**。一个模块一个职责，`routers/` 只做参数校验后调这里的函数。
依赖规则：可以 import `core/`、`db`、`config`，**不允许** import `routers/`。

```
services/
├─ parser.py             文件名 / 海报 / 简评评分解析
├─ scanner.py            扫描建索引（全量 / 定点、幂等、可取消）
├─ backfill.py           按目录结构回填年份 / 日期 / 制作组
├─ tagging.py            本机简评文本打标（离线，走题材词典）
├─ tagdict.py              题材词典与标签规范化（TAG_LIMIT）
├─ metadata_provider.py  联网元数据解析器（本地 / 百度 / sample 级联）
├─ completion.py         联网补全工作器（尊守 edited_fields，候选制）
├─ covers.py             本地封面匹配 + 指定目录补全 + 视频帧封面标记
├─ online_covers.py      联网封面搜索 / 下载 / 人工审定候选
├─ frames.py             ffmpeg 服务端预抽帧（落盘小图写回 poster_path）
├─ recommend.py          播放页推荐轮播：多因子加权打分 + 分页编排
├─ tag_restore.py        按导出 JSON 还原 media_tags 关联（只重建 id↔id）
├─ streaming.py          HTTP Range 流式播放 + 内存守卫 + 本地打开
└─ jobs.py               长任务互斥运行器（进度 / 取消）
```

---

## 模块详解

### 1. `parser.py` — 解析（无副作用，最好测）
- `parse_video_filename(name)`：解析三时代文件名规则 → 标题 / 制作组 / 日期 / 副标题标记。
- `normalize_title(s)`：标题归一（去冗余后缀 / 全半角 / 空白）。
- `derive_year_from_path(path)`：从目录结构派生年份 / 月份。
- `find_poster(video_path)`：封面查找（同名 → 上级目录 → 标题模糊）。
- `normalize_score(text)` / `parse_jianping(text)`：简评评分换算（★★☆、4 星半、箭头、双档各种写法，统一归一到库内 `rating_norm`）。
- **改文件名规则 / 评分换算规则 → 本文件**，且务必补 `tests/test_parser.py` 用例。
- 视频扩展名白名单 `VIDEO_EXTS`、图片 `IMAGE_EXTS`、字幕 `SUBTITLE_EXTS` 都在文件顶部。

### 2. `scanner.py` — 扫描
- `do_scan(cfg, dry_run, progress, scope, path, stop, ...)`：主入口。
  `scope="full"` 扫全部 `roots`；`scope="path"` 只处理指定目录。
- 幂等：以 `path + size + mtime` 生成指纹（`_hash`），内容没变就跳过。
- 按目录分块（`_iter_chunks`）以便中途取消（协作式，检查 `stop()`）。
- `_category_for(path)` 决定作品归到哪个分类；`_match_ratings(con, cfg)` 回填简评评分。
- **定点扫描不会删除范围之外的记录**。
- 改扫描范围策略 / 指纹算法 / 分块大小 → 本文件。

### 3. `backfill.py` — 目录派生回填
`run_metadata_backfill(job)`：利用「文件夹已按年份 / 月份分类」这一事实，把缺失的 `year / publish_date / studio` 从路径补出来（不联网）。

### 4. `tagdict.py` — 标签词典
- `TAG_ENTRIES`：`(正则, 规范标签)` 列表，**所有词条都在这里**。
- `TAG_LIMIT = 10`：每个作品的标签数上限（业务上限，导入 / 批量打标都以此为准）。
- `pick_tags(texts)` / `filter_tags(tags)`：从文本或候选标签里挑规范标签。
- **加词条 / 改上限 → 本文件**（改动后建议跑 `tests/test_tagdict.py`）。

### 5. `tagging.py` — 离线打标
`run_tag_backfill(job)`：读磁盘「简评」txt，按 `tagdict` 词典提标签写库（不联网）。辅助函数 `collect_entries` / `tags_for_title`。

### 6. `metadata_provider.py` — 联网元数据
- 统一候选协议：`resolve(media_row) -> dict`。
- 解析器：`LocalResolver` → `BaiduResolver` → `SampleResolver`，由 `CascadeResolver` 级联调用。
- `validate_candidate()`：清洗候选，约束 `MAX_SYNOPSIS`(简介字数) / `MAX_TAGS`(候选 tag 数) / `MAX_TAG_LEN`(单 tag 长度) / 发布日期格式。
- `UA`：请求头 User-Agent。站点结构变了 → 改 `extract_sample_results` / `extract_sample_post` / `extract_baidu_synopsis`。
- **改简介 / 标签长度上限 → 文件顶部常量**（注意：业务上限另见 `tagdict.TAG_LIMIT`）。

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
- `set_video_frame_cover(cfg, ids, clear)`：把某作品标记为「用视频帧当封面」（`meta.cover_mode = VIDEO_FRAME_MODE`）。
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
- 常量 `FRAME_WIDTH = 512`（抽帧宽度，够网格 / 详情用）—— **想更清晰改这里**。

### 11. `recommend.py` — 播放页推荐（**权重要调就在这**）
- 8 因子线性加权，常量都在文件顶部：`W_TAG` / `W_STUDIO` / `W_CATEGORY` / `W_YEAR` / `W_RATING` / `W_STATUS` / `W_FRESH` / `JITTER`。
- 多样性：`MAX_SAME_STUDIO`(每页同制作组上限) / `STUDIO_PENALTY`(重复惩罚) / `POOL_FACTOR`(参与编排的候选倍数)。
- `_profile()` 取当前作品画像 → `_candidates()` 一次拉候选并算共享标签数 → `_score()` 打分 → `_arrange()` 逐页贪心编排 → `recommend()` 返回分页。
- ⚠️ 评分必须按「全库 `MIN/MAX` 区间」归一（库内 `rating_norm` 量纲**不是** 0~1）。
- 权重表详见 [../../docs/architecture.md](../../docs/architecture.md#推荐打分因子appservicesrecommendpy)。

### 12. `tag_restore.py` — 关联还原
`parse_export()` / `restore()` / `restore_from_file()`：按外部导出文件把 `media_tags` 的 **id↔id 关联**重建回去。
**只重建关联，不写入也不保留标签文本**。

### 13. `streaming.py` — 播放
- `stream_file(path, request, chunk)`：HTTP Range 流式响应（分块 `CHUNK = 1MB`）。
- `guard_advice(media_row, cfg)`：按 `memory_guard_bytes` 给「是否建议本地打开」的建议。
- `open_local(path)`：调系统默认播放器（受 `default_player_open` 开关约束）。

### 14. `jobs.py` — 长任务运行器
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
| 改标签词条 / 每作品标签数上限 | `tagdict.py`（`TAG_ENTRIES` / `TAG_LIMIT`） |
| 改简介 / 标签长度约束 | `metadata_provider.py` 顶部常量 |
| 改封面匹配阈值 | `covers.py :: resolve(threshold=)` 调用处 / 默认值 |
| 改抽帧尺寸 | `frames.py :: FRAME_WIDTH` |
| **调推荐权重 / 翻页数** | `recommend.py` 顶部常量 + `webui/player.html` 的 `PER_PAGE` / `MAX_PAGES` |
| 改流式分块大小 | `streaming.py :: CHUNK` |
| 加一个新长任务 | `jobs.py` 的 `Job` + `routers/admin.py` 注册接口 |
