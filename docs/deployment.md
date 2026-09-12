# 项目部署前置要求和部署操作流程

> 适用版本：视频管理器（Python + FastAPI + SQLite 本机/内网 Web 应用）

## 一、部署前置要求

### 1. 运行环境

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（当前仅适配 Windows，路径含盘符） |
| Python | **3.11+**（实测 3.11 / 3.13 均可） |
| 数据库 | SQLite（内置，无需安装），运行于 WAL 模式 |
| 端口 | 默认 `8080`（可在 config.json 修改） |
| 磁盘 | 数据库 + `frames_cache/` + `cover_cache/` + `logs/` 需可写 |

### 2. 依赖安装

```powershell
pip install -r requirements.txt
```

依赖清单（`requirements.txt`）：

```
fastapi>=0.110          # Web 框架
uvicorn>=0.29           # ASGI 服务器
pydantic>=2.6           # 数据校验
pytest>=8.0             # 测试
imageio-ffmpeg>=0.4.9   # 捆绑 ffmpeg 二进制（服务端预抽帧封面）
Pillow>=10.0            # 图片像素分析（抽帧亮度判定 + 联网封面校验）
```

### 3. ffmpeg 来源说明（重要）

服务端「⑤ 服务端抽帧」依赖 ffmpeg，来源二选一：

1. **imageio-ffmpeg（推荐，默认）**：`pip install imageio-ffmpeg` 后自动捆绑 ffmpeg 二进制，**无需手动安装**。
2. **系统安装 ffmpeg（可选）**：手动装好后在 `config.json` 里填 `"ffmpeg_path": "C:\\...\\ffmpeg.exe"`，程序优先使用它。

> ⚠️ **关键注意**：项目可能被多个 Python 环境启动（系统 Python / managed Python / IDE 内置 Python 等）。
> 依赖必须装到**实际启动服务所用的那个 Python** 里。若抽帧全部失败并报
> `ModuleNotFoundError: No module named 'imageio_ffmpeg'`，说明依赖装错了 Python 环境，
> 请用启动服务的同一个 `python -m pip install -r requirements.txt` 重装。

### 4. 一键启动脚本与环境自检

推荐用仓库自带脚本启动，它会**自动挑出装了依赖的那个 Python**，避免「多 Python 环境装错依赖」这一经典问题。

```powershell
powershell -ExecutionPolicy Bypass -File run.ps1      # Windows（或双击 run.bat）
./run.sh                                              # macOS / Linux
```

**解释器查找顺序**（找到「能 import fastapi, uvicorn」的即止）：
`-Python` 参数 → 环境变量 `VM_PYTHON` → 项目根 `.python-path`（一行绝对路径）→ `.venv\Scripts\python.exe` → PATH 中的 `python` / `python3` → `%LOCALAPPDATA%\Programs\Python\Python*`。
若全都没装依赖，则用 PATH 里的 `python` 安装 `requirements.txt` 后重试。

**指定解释器**（多 Python 机器上建议固定下来）：

```powershell
# 方式 A：命令行传参
powershell -ExecutionPolicy Bypass -File run.ps1 -Python "D:\Python311\python.exe"
# 方式 B：在项目根建 .python-path 文件，写入解释器绝对路径（已在 .gitignore 中，不会提交）
```

**只自检、不启动**（排查问题时先跑这个，会依次验证解释器 / 依赖 / 配置 / 数据库）：

```powershell
powershell -ExecutionPolicy Bypass -File run.ps1 -CheckOnly
```

> ⚠️ **三个必知的坑**
> 1. **依赖只装在其中一个 Python 里**：直接 `python -m uvicorn ...` 很可能命中没装依赖的那个，报
>    `ModuleNotFoundError: No module named 'fastapi'`。请用 `run.ps1`，或显式指定解释器。
> 2. **`.ps1` 必须是 UTF-8 with BOM**：Windows PowerShell 5.1 读取**无 BOM** 的脚本时按系统 ANSI(GBK) 解码，
>    脚本里的中文会乱码并破坏引号配对，直接报「字符串缺少终止符」而跑不起来。
>    本仓库 `run.ps1` 已带 BOM，**修改后请保持 BOM**（VS Code 右下角编码选 “UTF-8 with BOM”）。
> 3. **`.bat` 必须 CRLF 换行 + 纯 ASCII 输出**：LF 换行的批处理会被 cmd 当成一整行处理，
>    症状是**双击后窗口一闪而过、没有任何输出**；而 `chcp 65001` 配合非 ASCII 文本在切换代码页时
>    也容易解析出错。因此 `run.bat` 刻意保持「CRLF + 英文提示」，**中文提示统一由 `run.ps1` 输出**。

### 5. 配置文件（config.json）

首次部署前编辑 `config.json`（或 `config.example.json` 复制改名）：

```json
{
  "roots": ["D:\\MediaLibrary"],   // 扫描根目录（放视频文件的盘/目录）
  "category_filter": ["视频"],              // 仅索引这些分类（扫描时按目录名判定）
  "db_path": "...\\library.db",            // SQLite 库路径
  "host": "127.0.0.1",
  "port": 8080,
  "memory_guard_bytes": 2147483648,         // 播放体积阈值(2GB)，超此回退本地播放器
  "default_player_open": true,              // 是否允许一键本地播放器打开
  "default_user_id": 1,                     // 默认用户（v1 固定 admin）
  "ffmpeg_path": "",                        // 留空则用 imageio-ffmpeg 捆绑二进制
  "frames_dir": "frames_cache",             // 抽帧小图缓存目录（相对项目根）
  "log_level": "INFO",                      // DEBUG/INFO/WARNING/ERROR
  "log_dir": "logs"                         // 运行日志目录（相对项目根）
}
```

## 二、部署操作流程

### 首次部署

```powershell
# 1) 安装依赖
pip install -r requirements.txt

# 2) 初始化数据库（建表 + 迁移 + seed 默认 admin）
python -m app.cli init

# 3) 编辑 config.json（roots / category_filter / ffmpeg_path 等）

# 4) 启动服务
powershell -ExecutionPolicy Bypass -File run.ps1
# 或直接：
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

启动成功后浏览器访问 `http://localhost:8080`。

### 云服务 / 容器部署

- 纯 `pip install -r requirements.txt` 即可，**无需安装系统级 ffmpeg**（imageio-ffmpeg 已捆绑）。
- 容器化要点：挂载 `roots`（媒体目录，需与宿主路径一致）、`library.db`、`config.json` 为卷。
- `host` 改为 `0.0.0.0` 以对外监听（注意：v1 免登录，公网部署前务必接入鉴权层，见架构文档「未来 todo」）。

### 升级 / 迁移

- **改 Python 代码后**需重启服务（前端是静态文件，刷新浏览器即可生效）。
- **数据库升级**：`python -m app.cli init` 幂等执行 `schema.sql` 并自动补缺失列，不会丢数据。
- **ffmpeg 缺失时的降级**：服务端抽帧任务会失败并推送通知；此时前端仍保留浏览器离屏抓帧兜底（`cover_mode=video_frame` 作品仍能显示，只是慢）。

### 停止服务（Windows）

**方式一：脚本（推荐）**

```powershell
# 双击 stop.bat，或：
powershell -ExecutionPolicy Bypass -File stop.ps1
# 跳过确认：
powershell -ExecutionPolicy Bypass -File stop.ps1 -Force
# 非默认端口：
powershell -ExecutionPolicy Bypass -File stop.ps1 -Port 9000
```

`stop.ps1` 会从 `config.json` 读端口，列出正在监听该端口的进程并等你确认，结束后再校验端口是否真的释放。

> 为什么按**端口**找而不是按进程名？`Get-Process python | Stop-Process` 会把你 IDE、其它脚本用的 Python 一起杀掉。按端口定位只动服务自己。

**方式二：手动命令**

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

> `pkill` 在 Git Bash 下对原生 Windows 进程不可靠，不要用它。

**方式三：前台运行时** —— 直接在跑服务的窗口里按 `Ctrl+C`，或关掉那个窗口。

**方式四：任务管理器** —— 详细信息里找到监听 8080 的 `python.exe` 结束任务。

## 三、运行期产生的目录

| 目录 | 说明 |
|---|---|
| `library.db` (+ `-wal`/`-shm`) | SQLite 主库（WAL 模式） |
| `logs/` | 应用运行日志（按天滚动，保留 7 份） |
| `frames_cache/` | 服务端抽帧小图（每作品一张 jpg，写回 poster_path） |
| `cover_cache/` | 联网下载的封面缓存 |
| `backups/` | 标签覆盖导入前的自动备份（tags/media_tags 各一份带时间戳） |
