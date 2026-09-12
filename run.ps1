# 视频管理器 · 一键启动（Windows / PowerShell）
#
# 用法：
#   双击 run.bat            （最简单）
#   右键本文件 → 使用 PowerShell 运行
#   命令行: powershell -ExecutionPolicy Bypass -File run.ps1
#   指定端口: powershell -ExecutionPolicy Bypass -File run.ps1 -Port 9000
#
# 它做四件事：检查 Python → 装依赖 → 生成/校验配置 → 初始化数据库 → 启动服务。

param(
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }

Say "==========================================" "DarkGray"
Say "  视频管理器 · 一键启动" "Cyan"
Say "==========================================" "DarkGray"

# ---------- 1/4 Python ----------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Say "[×] 未找到 python。请安装 Python 3.11+ 并勾选 'Add to PATH'。" "Red"
    Say "    下载：https://www.python.org/downloads/" "Gray"
    exit 1
}

# ---------- 2/4 依赖 ----------
Say "[1/4] 检查依赖..." "Cyan"
python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Say "      缺少依赖，正在安装 requirements.txt ..." "Yellow"
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Say "[×] 依赖安装失败，请检查网络或 pip 源。" "Red"; exit 1 }
}

# ---------- 3/4 配置 ----------
Say "[2/4] 检查配置..." "Cyan"
if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
    Say "      已由模板生成 config.json —— 请按需修改其中的 roots（媒体根目录）！" "Yellow"
}

# ---------- 4/4 数据库 + 启动 ----------
Say "[3/4] 初始化数据库..." "Cyan"
python -m app.cli init

$bindHost = "127.0.0.1"
$bindPort = 8080
try {
    $cfg = Get-Content "config.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.host) { $bindHost = $cfg.host }
    if ($cfg.port) { $bindPort = [int]$cfg.port }
} catch {
    Say "      config.json 解析失败，改用默认 127.0.0.1:8080" "Yellow"
}
if ($Port -gt 0) { $bindPort = $Port }

Say "[4/4] 启动服务： http://${bindHost}:${bindPort}    (Ctrl+C 退出)" "Green"
python -m uvicorn app.main:app --host $bindHost --port $bindPort
