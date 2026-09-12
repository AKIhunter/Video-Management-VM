# 视频管理器 · 停止服务
#
# 用法：
#   双击 stop.bat
#   powershell -ExecutionPolicy Bypass -File stop.ps1
#   powershell -ExecutionPolicy Bypass -File stop.ps1 -Port 9000
#   powershell -ExecutionPolicy Bypass -File stop.ps1 -Force      （跳过确认）
#
# 原理：按**端口**找出正在 LISTENING 的进程再结束它。
# 这比按进程名杀（Get-Process python | Stop-Process）精准得多 ——
# 后者会连 IDE、其它脚本用的 python 一起误杀。

param(
    [int]$Port = 0,
    [switch]$Force          # 不询问，直接结束
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }

# ---------- 确定端口：命令行 > config.json > 默认 8080 ----------
$bindPort = 8080
if (Test-Path "config.json") {
    try {
        $cfg = Get-Content "config.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.port) { $bindPort = [int]$cfg.port }
    } catch { }
}
if ($Port -gt 0) { $bindPort = $Port }

Say "==========================================" "DarkGray"
Say "  视频管理器 · 停止服务" "Cyan"
Say "==========================================" "DarkGray"
Say "目标端口： $bindPort" "Gray"

# ---------- 找出监听该端口的进程 ----------
$procIds = @(Get-NetTCPConnection -LocalPort $bindPort -State Listen -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique)

if ($procIds.Count -eq 0) {
    Say "[i] 端口 $bindPort 上没有正在监听的服务，无需停止。" "Yellow"
    exit 0
}

foreach ($procId in $procIds) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    $name = "(已退出)"
    if ($proc) { $name = $proc.ProcessName }
    Say "待结束： PID = $procId    进程 = $name" "Gray"
}

if (-not $Force) {
    $ans = Read-Host "确认结束以上进程？(Y/N)"
    if ($ans -notmatch "^[Yy]") {
        Say "已取消，服务保持运行。" "Yellow"
        exit 0
    }
}

# ---------- 结束并等待端口释放 ----------
foreach ($procId in $procIds) {
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Say "[√] 已结束 PID = $procId" "Green"
    } catch {
        Say "[×] 结束 PID = $procId 失败：$($_.Exception.Message)" "Red"
    }
}

for ($i = 1; $i -le 10; $i++) {
    Start-Sleep -Milliseconds 400
    if (-not (Get-NetTCPConnection -LocalPort $bindPort -State Listen -ErrorAction SilentlyContinue)) { break }
}

if (Get-NetTCPConnection -LocalPort $bindPort -State Listen -ErrorAction SilentlyContinue) {
    Say "[!] 端口 $bindPort 仍被占用，请稍后重试，或用任务管理器结束对应进程。" "Red"
    exit 1
}
Say "[√] 端口 $bindPort 已释放，服务已停止。" "Green"
