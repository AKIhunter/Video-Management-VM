@echo off
rem 视频管理器 · 一键启动（Windows 双击即用）
rem 内部调用同目录的 run.ps1，并把输出保留在窗口里。
chcp 65001 >nul
cd /d "%~dp0"

echo 正在启动 视频管理器 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*

echo.
echo 服务已退出。按任意键关闭窗口。
pause >nul
