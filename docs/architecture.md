# 项目架构 & 开发须知 & 未来 todo

## 零、新开发者阅读路线（从这里开始）

按顺序走一遍，约 1 小时即可建立完整心智模型。

### 第 1 步 · 先把它跑起来（15 分钟）
1. 按 [deployment.md](deployment.md) 装依赖、复制 `config.example.json` 为 `config.json` 并改 `roots`；
2. `powershell -ExecutionPolicy Bypass -File run.ps1` 启动，打开 <http://localhost:8080>；
3. 点一遍主流程：**扫描 → 首页筛选 → 打开详情 → 播放 → 白屏管理**；
4. 跑 `python -m pytest tests/ -q`，**全绿再往下**（这是你后续改代码的安全网）。

### 第 2 步 · 建立目录地图（10 分钟）
每个目录都有自己的 README，**只读「职能」和「常见修改点速查」两节**就够：

| 目录 | README | 一句话职责 |
|---|---|---|
| `app/` | [app/README.md](../app/README.md) | 后端根：装配、配置、DB、权限、CLI、日志 |
| `app/core/` | [app/core/README.md](../app/core/README.md) | 无业务语义的公共工具（编码读取、404 查询） |
| `app/services/` | [app/services/README.md](../app/services/README.md) | **全部业务逻辑**（14 个模块，改动主要发生在这里） |
| `app/routers/` | [app/routers/README.md](../app/routers/README.md) | HTTP 接口层（薄，只做校验与转发） |
| `webui/` | [webui/README.md](../webui/README.md) | 零构建前端（主页面 + 播放页 + 主题变量） |
| `tests/` | [tests/README.md](../tests/README.md) | pytest 用例与编写约定 |
| `docs/` | [README.md](README.md) | 文档自身的边界与维护义务 |
| `frames_cache/` `cover_cache/` `logs/` `backups/` | 各自目录内 README | **运行时生成物**，删除前请先读它的 README |

### 第 3 步 · 抓住三条主线（30 分钟）

**① 入库线（数据从磁盘进入数据库）**
`routers/admin.py::scan` → `services/scanner.py::do_scan()`（分块、幂等、可取消）
→ `services/parser.py`（文件名 / 年份 / 简评评分）→ 写 `media` + `media_tags` / `tags`。

**② 展示线（数据从数据库到页面）**
`webui/index.html` + `app.js` → `routers/media.py`（`_filters()` 拼 WHERE、`serialize()` 出结构）
→ `services/*` → SQLite。播放则走 `routers/playback.py` → `services/streaming.py`。

**③ 封面与推荐线（两条最容易被误解的支线）**
- 封面四级兜底：`covers.py`（本地匹配 / 指定目录）→ `frames.py`（ffmpeg 抽帧落盘）→ `online_covers.py`（联网 + 人工审定）。
- 播放页推荐：`services/recommend.py`（8 因子打分 + 分页编排）→ `GET /api/media/{id}/recommend` → `webui/player.html` 的 2×6 轮播。

### 第 4 步 · 动手前的三条纪律
1. **改 `services/` 前先看它的 README**，多数「可调参数」都集中在模块顶部常量区；
2. **改完必跑 `pytest`**，新逻辑补一个 `tests/test_<模块>.py`（模板见 [tests/README.md](../tests/README.md)）；
3. **改 `app/` 要重启服务**（页面右上角「一键重启」），只改 `webui/` 刷新浏览器即可。

---

## 一、架构总览

**技术栈**：Python 3.11+ / FastAPI / Uvicorn / SQLite(WAL+FTS5) / 零构建原生 JS 单页。

```
视频管理器/
├─ app/                     # 📖 详见 app/README.md
│  ├─ main.py            # 入口：FastAPI app + lifespan(日志/DB初始化) + /api/poster 兜底 + 静态挂载
│  ├─ config.py          # 配置：config.json 读写 + 默认值 + 进程级缓存
│  ├─ db.py / schema.sql # 数据层：连接(WAL)、建表/迁移、分类同步
│  ├─ authz.py           # 权限：current_user / require_admin（v1 固定 admin，预留多用户）
│  ├─ logging_setup.py   # 日志：TimedRotatingFileHandler 落盘 logs/app.log
│  ├─ cli.py             # CLI：init / scan / tags-restore
│  ├─ core/              # 📖 详见 app/core/README.md —— 基础层（无业务状态，跨模块复用）
│  │  ├─ media_helpers.py  # edited() 解析、media_or_404()
│  │  └─ fsutils.py        # read_utf8() 编码探测读、find_jianping_files()
│  ├─ services/          # 📖 详见 app/services/README.md —— 业务逻辑层（14 个模块）
│  │  ├─ parser.py        # 文件名/简评解析、年份派生
│  │  ├─ scanner.py       # 扫描建索引（全量/增量、幂等、分类覆盖）
│  │  ├─ metadata_provider.py # 联网元数据解析器（在线/Sample/百度）
│  │  ├─ completion.py    # 联网补全（日期/简介/标签，尊重 edited_fields）
│  │  ├─ tagging.py       # 本机简评打标（题材词典）
│  │  ├─ tagdict.py         # 题材词典、TAG_LIMIT、filter_tags
│  │  ├─ covers.py        # 本地封面匹配 + 指定目录补全 + 视频帧标记
│  │  ├─ online_covers.py # 联网封面搜索/下载/审定
│  │  ├─ frames.py        # ffmpeg 服务端预抽帧（落盘小图写回 poster_path）
│  │  ├─ backfill.py      # 元数据回填（年份/日期由路径派生）
│  │  ├─ tag_restore.py   # 关联还原（按导出 JSON 重建 media_tags）
│  │  ├─ recommend.py     # 播放页推荐轮播：多因子加权打分 + 每页 2×6 编排
│  │  ├─ streaming.py     # HTTP Range 流式播放
│  │  └─ jobs.py          # 长任务互斥运行器（Job + run_exclusive）
│  └─ routers/           # 📖 详见 app/routers/README.md —— 路由层（薄：参数校验 + 调用服务）
│     ├─ admin.py         # 后台管理聚合路由（约 1500 行，内部按职责分段）
│     ├─ media.py         # 首页列表/详情/标签/统计/推荐
│     ├─ playback.py      # 播放/下载/本地打开
│     └─ userdata.py      # 观看状态/评分/收藏
├─ webui/               # 📖 详见 webui/README.md —— 零构建前端
│                       #   index.html + app.js + style.css + player.html + logs.html + assets/
├─ tests/               # 📖 详见 tests/README.md —— pytest（15 文件，不启动服务、临时库）
├─ docs/                # 📖 详见 docs/README.md —— 三份主文档 + 文档索引
│                       #   deployment.md / user-manual.md / architecture.md
├─ frames_cache/        # 📖 运行时生成物：ffmpeg 抽帧小图（不入库）
├─ cover_cache/         # 📖 运行时生成物：联网审定采用的封面（不入库，勿随手删）
├─ logs/                # 📖 运行时生成物：按天滚动的 app.log（不入库）
├─ backups/             # 📖 导出快照（⚠️ 含标签文本，禁止提交，见目录内 README）
├─ config.example.json  # 配置模板；config.json 为开发者本机配置（不入库）
├─ requirements.txt
└─ run.ps1 / run.bat / run.sh   # 一键启动（三平台）
   stop.ps1 / stop.bat          # 一键停止（按端口定位进程，避免误杀其它 python）
```

**分层依赖方向**（单向，避免循环）：`routers → services → core`，`services → db/config`，`core` 不依赖业务层。

## 二、数据模型（SQLite，11 表 + 1 FTS）

| 表 | 用途 |
|---|---|
| `users` | 用户（v1 固定 id=1 admin，预留多用户） |
| `settings` | 键值设置（如联网封面范围 cover_online_scope） |
| `media` | **作品单表**：基础信息 + 索引ID + poster_path + meta + edited_fields |
| `media_fts` | FTS5 全文索引（标题/制作组） |
| `tags` | 标签字典：id → 标签值 |
| `media_tags` | 作品-标签**关联表**：只存 `(media_id, tag_id)`，**无 name 列** |
| `watch_state` | 用户观看状态/评分/收藏/笔记 |
| `scan_runs` | 扫描运行记录 |
| `cover_reviews` | 联网封面人工审定候选 |
| `notifications` | 消息通知（任务完成/失败/重启） |
| `categories` | 分类字典（扫描分类/作品管理/页面筛选三处共用） |

**关键字段**：
- `media.meta`：JSON，含 `auto_tags`（简评打标留痕）、`cover_mode`（video_frame）、`online_cover`（联网封面来源）等。
- `media.edited_fields`：JSON 数组，记录人工编辑过的字段（`synopsis`/`tags`/`poster_path` 等），联网补全**永不覆盖**这些字段。

## 三、核心数据流

1. **扫描**：`scan → scanner.do_scan → parser 解析文件名 → 写入 media → 匹配本地封面/简评评分`。
2. **联网补全**：`completion.run_completion → metadata_provider 联网 → 候选入库（尊重 edited_fields）`。
3. **封面**：`本地匹配 / 联网下载 / 指定目录 / 服务端抽帧(ffmpeg) → 写 poster_path → 前端 /api/poster/{id} 出图`。
4. **长任务**：`jobs.Job(run_exclusive 互斥) → worker 分块执行 + job.tick 进度 + job.stop 取消`，完成/失败推 `notifications`。
5. **播放页推荐**：`player.html → GET /api/media/{id}/recommend → recommend.recommend()` 多因子打分排序后按每页 12 格编排成 3 页，前端以 `translate3d` 分页轮播（2 行 × 6 列，不滚动）。

### 推荐打分因子（`app/services/recommend.py`）

| # | 因子 | 权重常量 | 说明 |
|---|------|---------|------|
| 1 | 标签重合度 | `W_TAG=4.0` | `共享标签数 / 当前作品标签数`，主因子（**只算 COUNT，不读标签文本**） |
| 2 | 同制作组 | `W_STUDIO=1.6` | 系列 / 画风一致性 |
| 3 | 同分类 | `W_CATEGORY=0.5` | 弱加成，避免过度同质 |
| 4 | 年代接近 | `W_YEAR=0.9` | `\|Δyear\|≤10` 线性衰减 |
| 5 | 评分质量 | `W_RATING=1.2` | 按**全库评分区间**归一（库内量纲 1~6，非 0~1） |
| 6 | 观看状态 | `W_STATUS=0.4` | 看完 −2×；想看 / 在看 / 未看 加分 |
| 7 | 入库新鲜度 | `W_FRESH=0.35` | 一年内线性衰减 |
| 8 | 探索扰动 | `JITTER=0.9` | 随机抖动，「换一批」靠换 seed 实现 |

编排：`_arrange()` 逐格贪心，`等效分 = 原始分 − STUDIO_PENALTY(1.5) × 该制作组本页已用格数`，达 `MAX_SAME_STUDIO=3` 后施加巨额惩罚（仅在无人可选时突破），保证每页填满且同一系列不包场。

## 四、开发须知

### 1. 测试风格
- `pytest tests/`（当前 151 passed）。
- **不启动服务**：直接 import 模块函数调用，`monkeypatch` 替换 `config.load` 指向临时库。
- 需要 ffmpeg 的测试（`test_frames.py`）在无 ffmpeg 环境自动 `pytest.skip`；需要 Node 的（`test_frame_stats_js.py`）同理。
- 推荐算法的测试用 `seed=` 固定随机种子，断言确定性；多样性约束断言「每页同制作组 ≤ MAX_SAME_STUDIO」。

### 2. 项目铁律（务必遵守）
- **禁止读取作品表完整内容 / 禁止读取标签值**：排查/测试只允许读 schema、id 列、聚合计数。
- **不移动 / 删除 / 重命名源媒体文件**：只读索引与个人化数据。
- **edited_fields 永不联网覆盖**：人工编辑过的字段，补全/打标一律跳过。
- **全盘扫描 / 一键重启 / 覆盖导入** 必须二次确认。
- **文本文件编码**：导出用 UTF-8 带 BOM（`utf-8-sig`）；导入按原始字节探测 `utf-8-sig → utf-8 → gbk → big5`，拒绝含 U+FFFD 的内容。
- **不引入无谓依赖**：联网用 stdlib urllib；文件上传走 base64/JSON body（避免 python-multipart）。
- **禁止提交敏感数据**：`library.db`、`config.json`、`frames_cache/`、`cover_cache/`、`backups/`、`logs/` 一律不入库（见根目录 `.gitignore`）。

### 3. 运行与调试
- 一键启动：`run.ps1`（Windows）/ `run.bat`（双击）/ `run.sh`（macOS / Linux）。
- 跑测试/起服务用**同一个 Python**（注意项目可能被多个 Python 环境启动，依赖要装到实际启动服务的那个）。
- **改 `app/` 下 Python 代码需重启**；只改 `webui/` 则刷新浏览器即可。
- **Windows 停服务**：`Get-NetTCPConnection -LocalPort 8080 -State Listen | ... Stop-Process`（`pkill` 在 Git Bash 下不可靠）。

### 4. 文档义务
改了功能就同步改文档，边界划分见 [docs/README.md](README.md)：

| 改动类型 | 要更新 |
|---|---|
| 新增/修改接口 | 本文档 + `app/routers/README.md` |
| 新增/修改配置项 | `deployment.md` + `config.example.json` + `app/README.md` |
| 用户可见功能 | `user-manual.md` |
| 新增业务模块 / 可调常量 | 对应目录 README（`services/README.md` 等） |
| 界面与交互 | `webui/README.md` |

## 五、未来 todo

- [ ] **多用户 / 角色接入**：当前 `users` 表已预留，但 v1 免登录固定 admin；接入登录层后 `require_admin` 自动守卫。
- [ ] **公网部署鉴权**：上线前补齐鉴权（v1 无登录，公网暴露有风险）。
- [ ] **admin.py 拆分**：约 1500 行的聚合路由可按职责拆为 admin_media / admin_tags / admin_categories / admin_covers / admin_tasks / admin_notifications / admin_restart（共享 helper 已部分下沉 core）。
- [ ] **WebSocket 实时进度**：长任务进度当前靠前端轮询（1.5s），可改为 WebSocket 推送。
- [ ] **转码 / 在线流**：`streaming.py` 当前只做 Range 直传，无转码；未来可按需增加。
- [ ] **只读副本 / 缓存**：为公网多用户预留只读副本与封面 CDN 缓存。
- [ ] **容器化**：当前 `run.ps1` 直接跑 uvicorn；可提供 Dockerfile（纯 pip + 卷挂载 roots/library.db/config.json）。
- [ ] **Pillow 依赖正式化**：`online_covers.py` 的 `from PIL import Image` 已补入 requirements.txt，需在干净环境回归验证。
- [ ] **纯净版同步**：对外发布的脱敏副本（`视频管理器`）需在每次功能更新后同步代码，并保持 `config.example.json` 与文档中的示例路径中性化。
