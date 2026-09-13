# 视频管理器

> 轻量级本地 / 内网媒体索引与查询管理器 —— **Python + FastAPI + SQLite(WAL) + 零构建原生前端**

把 `D:\MediaLibrary` 下散落的媒体文件夹，变成「可检索、可评分收藏、可在线/本地播放、可联网补全简介与封面」的索引应用。
单进程异步、无前端构建步骤、无外部服务依赖（ffmpeg 可选）。

---

## 📚 文档导航（新同学先看这里）

| 文档 | 讲什么 | 适合谁 |
|---|---|---|
| **[docs/deployment.md](docs/deployment.md)** | 部署前置要求、完整部署流程、ffmpeg 从哪来、Python 环境坑 | 第一次把这套跑起来的人 |
| **[docs/user-manual.md](docs/user-manual.md)** | 全部功能的使用方法与操作路径（含播放页推荐轮播） | 使用者 |
| **[docs/architecture.md](docs/architecture.md)** | 架构分层、数据模型、开发须知、**新开发者阅读路线**、未来 todo | 要改代码的人 |
| **[docs/README.md](docs/README.md)** | 文档目录自身的组织方式与维护约定 | 写文档的人 |

**代码目录的职责、实现方式、以及「想改变量该去哪儿改」，都在各目录自己的 README 里：**

[`app/`](app/README.md) · [`app/core/`](app/core/README.md) · [`app/services/`](app/services/README.md) · [`app/routers/`](app/routers/README.md) · [`webui/`](webui/README.md) · [`tests/`](tests/README.md)

**运行时目录（生成物，不提交）：** [`frames_cache/`](frames_cache/README.md) · [`cover_cache/`](cover_cache/README.md) · [`logs/`](logs/README.md) · [`backups/`](backups/README.md)

---

## 🚀 快速开始

```powershell
# Windows 一键启动：检查依赖 → 初始化数据库 → 启动服务
powershell -ExecutionPolicy Bypass -File run.ps1
```

也可以直接双击 `run.bat`（内部同样是调 `run.ps1`）。
跨平台（macOS / Linux）用 `./run.sh`。

> 启动脚本会**自动挑选「已装依赖」的 Python 解释器**（多 Python 环境的必踩坑）；
> 只想先做环境自检不启动服务，加 `-CheckOnly`。详见 [docs/deployment.md](docs/deployment.md#4-一键启动脚本与环境自检)。

启动后打开 <http://localhost:8080>。v1 为单用户免登录，默认管理员 `admin`。

### 停止服务

```powershell
# 双击 stop.bat，或：
powershell -ExecutionPolicy Bypass -File stop.ps1          # 会列出进程并让你确认
powershell -ExecutionPolicy Bypass -File stop.ps1 -Force   # 跳过确认
```

脚本按 `config.json` 里的**端口**定位进程（不会误杀你 IDE 里的 Python），结束后自动校验端口是否释放。
其它方式与原理详见 [docs/deployment.md](docs/deployment.md#停止服务windows)。

### 命令行（可选）

```powershell
python -m app.cli init                                    # 初始化 / 迁移数据库
python -m app.cli scan --dry-run                          # 干跑扫描（不写库）
python -m app.cli scan --scope path --path "D:\…\某目录"   # 定点扫描指定目录
python -m app.cli tags-restore --file tags_export.json     # 按导出文件还原标签关联
python -m pytest tests/ -q                                # 运行单元测试
```

---

## 🗂 目录结构

```
视频管理器/
├─ app/                     后端（分层：main → routers → services → core → db/config）
│  ├─ main.py               FastAPI 装配 / lifespan / 静态资源挂载
│  ├─ config.py             config.json 读写 + 默认值 + 进程级缓存
│  ├─ db.py                 连接(WAL) / 建表 / 迁移 / settings / sync_categories
│  ├─ authz.py              角色守卫（v1 固定 admin，为多用户预留）
│  ├─ cli.py                命令行入口（init / scan / tags-restore）
│  ├─ logging_setup.py      日志落盘 logs/app.log（按天滚动）
│  ├─ core/                 无业务依赖的公共工具
│  ├─ services/             全部业务逻辑（17 个模块）
│  └─ routers/              HTTP 接口层（薄：参数校验 + 调用 service）
├─ webui/                   零构建前端（原生 JS 单页 + 独立播放页）
├─ tests/                   pytest（不启动服务，临时库）
├─ docs/                    三份主文档 + 文档索引
├─ config.json              ← 本机配置（不入库，见 config.example.json）
├─ requirements.txt
├─ run.ps1 / run.bat / run.sh
└─ library.db               运行后生成（不入库）
```

---

## ✨ 功能概览

| 模块 | 说明 |
|---|---|
| 自动扫描建索引 | 全量 / 增量扫描（`path+size+mtime` 指纹），支持**全盘或指定路径**，按目录分块、可取消；解析文件名三时代规则、海报、简评评分，建 FTS5 全文索引 |
| 检索与筛选 | 关键词(FTS5)、年份、制作组、分类、评分下限、观看状态、只看收藏、多字段排序、分页、**标签多选（OR）**；筛选栏**刷新后保留**（会话内有效）；未手动翻页时**滚动到底自动加载**（带内存上限保护） |
| 标签体系 | 字典表 + 关联表分离（`tags` / `media_tags`）；页眉「标签气泡」多选筛选；批量打标签；导出/导入（覆盖导入会**快照重绑**，不脱绑） |
| 作品管理 | 分类字典 CRUD、勾选批量移动/删除索引/打标签、按 ID / 名称 / 标签模糊查询，**重复作品检测**（标题相似度分组 → 人工删重复索引） |
| 评分（0~10） | 详情页**仅点星评分**（10 颗星，点第 N 颗得 N 分）；每用户每作品一条记录、重复评分覆盖；后端即时重算 + 空闲统计**多用户平均分**；卡片显示最新评分；历史分数已 ×2 换算 |
| 账号与个人中心 | 管理中心账号**增删改查**（默认账号不可删 / 不可降级）；个人中心：**每日签到升等级**（成长值，连续签到额外奖励）、**个人收藏夹**、等级权益预留门槛（`services/levels.py`） |
| 个人化 | 观看状态 / 个人评分 / 收藏 / 备注（每账号一条记录，主键 `(media_id,user_id)`） |
| 播放 | 网页 HTTP Range 流式播放；超大文件守卫 → 回退「本地打开 / 下载」 |
| **播放页推荐轮播** | 8 因子加权打分 + 每页 2×6 网格自动轮播（详见 [webui/README.md](webui/README.md#播放页-playerhtml)） |
| 封面 | 本地匹配 → 指定目录补全 → **ffmpeg 服务端预抽帧** → 联网搜索（人工审定），四级兜底 |
| 联网补全 | 缺资料时按 **本地 → 百度 → media-db.example.com** 级联；候选制，**永不覆盖人工编辑字段** |
| 白屏管理 | 作品编辑 / 作品管理（含重复检测）/ 联网补全 / 扫描 / 标签管理 / 一键重启（防误触二次确认） |
| 任务与通知 | 全局互斥单任务槽（进度 / 取消）+ 完成/失败推通知 |
| 统计 | 总数 / 分类 / 年度分布 / 平均分 / 标签使用数 |

---

## ⚙️ 配置（`config.json`）

首次运行请从 `config.example.json` 复制一份为 `config.json` 再改。

| 键 | 说明 |
|---|---|
| `roots` | 扫描根目录列表（可多个） |
| `category_filter` | 扫描 / 展示的分类白名单 |
| `db_path` | SQLite 数据库路径 |
| `host` / `port` | 监听地址与端口 |
| `memory_guard_bytes` | 超过该体积的播放回退到本地打开（默认 2 GB） |
| `default_player_open` | 是否允许「本地默认播放器打开」 |
| `default_user_id` | 默认用户（v1 = 1 / admin） |
| `ffmpeg_path` | 自定义 ffmpeg 路径；留空则用 `imageio-ffmpeg` 捆绑版 |
| `frames_dir` | 抽帧小图缓存目录（默认 `frames_cache`） |
| `log_level` / `log_dir` | 日志级别与目录 |

---

## 🔒 项目约定（改代码前必读）

- **只读索引，不动源文件**：不移动 / 删除 / 重命名磁盘上的媒体文件；`file_path` 仅白屏管理页可改。
- **候选制**：任何联网结果都不自动覆盖 `media.edited_fields` 中记录的人工编辑字段。
- **不额外引依赖**：联网用 stdlib `urllib`；文件传输走 base64/JSON body（避免 `python-multipart`）。
- **改 `app/` 下代码需重启**（页面右上角「一键重启」或重跑 `run.ps1`）；只改 `webui/` 刷新浏览器即可。
- **测试不启动服务**：直接 import 模块调用，`monkeypatch` 把 `config.load` 指向临时库。

详细开发规范与阅读路线见 **[docs/architecture.md](docs/architecture.md)**。
