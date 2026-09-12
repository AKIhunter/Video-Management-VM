# 视频管理器 · 一键启动（Windows / PowerShell）
#
# 用法：
#   双击 run.bat                                   （最简单）
#   右键本文件 → 使用 PowerShell 运行
#   powershell -ExecutionPolicy Bypass -File run.ps1
#   powershell -ExecutionPolicy Bypass -File run.ps1 -Port 9000
#   powershell -ExecutionPolicy Bypass -File run.ps1 -Python "D:\Python311\python.exe"
#
# ⚠️ 为什么需要「选择解释器」这一步：
#   一台机器上常常有多个 Python（系统 Python / 商店版 / IDE 内置 / 虚拟环境），
#   而依赖只会装在其中一个里。若直接 `python -m uvicorn`，PATH 里的那个未必装了
#   fastapi/uvicorn，就会报 ModuleNotFoundError 起不来。本脚本会逐个探测，
#   优先挑「已经装好依赖」的那个。
#
# 解释器查找顺序：
#   1) -Python 参数  2) 环境变量 VM_PYTHON  3) 项目根 .python-path 文件（一行路径）
#   4) .venv\Scripts\python.exe  5) PATH 中的 python / python3
#   6) %LOCALAPPDATA%\Programs\Python\Python*\python.exe
#   全都没装依赖时 → 用 PATH 的 python 安装 requirements.txt 再重试。

param(
    [int]$Port = 0,
    [string]$Python = "",
    [switch]$CheckOnly        # 只做环境自检（解释器/依赖/配置/数据库），不启动服务
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }

# ---------- 判断某个解释器是否已装好依赖 ----------
function Test-PyDeps($exe) {
    if (-not $exe) { return $false }
    if ($exe -eq "python" -or $exe -eq "python3") {
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    } elseif (-not (Test-Path $exe)) {
        return $false
    }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $exe -c "import fastapi, uvicorn" *> $null
        $ok = ($LASTEXITCODE -eq 0)
    } catch {
        $ok = $false
    }
    $ErrorActionPreference = $prev
    return $ok
}

# ---------- 按优先级找出可用解释器 ----------
function Resolve-Python($explicit) {
    $cands = New-Object System.Collections.Generic.List[string]
    if ($explicit) { $cands.Add($explicit) }
    if ($env:VM_PYTHON) { $cands.Add($env:VM_PYTHON) }

    $pin = Join-Path $PSScriptRoot ".python-path"
    if (Test-Path $pin) {
        $v = (Get-Content $pin -Raw -ErrorAction SilentlyContinue)
        if ($v) { $cands.Add($v.Trim()) }
    }

    $cands.Add((Join-Path $PSScriptRoot ".venv\Scripts\python.exe"))
    $cands.Add("python")
    $cands.Add("python3")

    $pyRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $pyRoot) {
        Get-ChildItem $pyRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { $cands.Add((Join-Path $_.FullName "python.exe")) }
    }

    foreach ($c in $cands) {
        if (Test-PyDeps $c) { return $c }
    }
    return $null
}

Say "==========================================" "DarkGray"
Say "  视频管理器 · 一键启动" "Cyan"
Say "==========================================" "DarkGray"

# ---------- 0/4 选择解释器 ----------
Say "[0/4] 选择 Python 解释器..." "Cyan"
$pyExe = Resolve-Python $Python
if ($pyExe) {
    Say "      使用： $pyExe" "Gray"
} else {
    Say "      [!] 没有找到已装依赖的解释器，将使用 PATH 中的 python 安装依赖。" "Yellow"
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Say "[×] 未找到 python。请安装 Python 3.11+ 并勾选 'Add to PATH'。" "Red"
        Say "    下载：https://www.python.org/downloads/" "Gray"
        exit 1
    }
    $pyExe = "python"
    Say "      实际解释器： $((Get-Command python).Source)" "Yellow"
    & $pyExe -m pip install -r requirements.txt
    if (-not (Test-PyDeps $pyExe)) {
        Say "[×] 依赖仍未就绪。请用 -Python 指定解释器，或在项目根写一行 .python-path。" "Red"
        exit 1
    }
}

# ---------- 1/4 依赖 ----------
Say "[1/4] 检查依赖..." "Cyan"
if (-not (Test-PyDeps $pyExe)) {
    Say "      缺少依赖，正在安装 requirements.txt ..." "Yellow"
    & $pyExe -m pip install -r requirements.txt
    if (-not (Test-PyDeps $pyExe)) { Say "[×] 依赖安装失败，请检查网络或 pip 源。" "Red"; exit 1 }
}

# ---------- 2/4 配置 ----------
Say "[2/4] 检查配置..." "Cyan"
if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
    Say "      已由模板生成 config.json —— 请按需修改其中的 roots（媒体根目录）！" "Yellow"
}

# ---------- 3/4 数据库 ----------
Say "[3/4] 初始化数据库..." "Cyan"
& $pyExe -m app.cli init

if ($CheckOnly) {
    Say "==========================================" "DarkGray"
    Say "[√] 环境自检通过：解释器、依赖、配置、数据库均正常。" "Green"
    Say "    去掉 -CheckOnly 即可启动服务。" "Gray"
    exit 0
}

# ---------- 4/4 启动 ----------
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
& $pyExe -m uvicorn app.main:app --host $bindHost --port $bindPort
