@echo off
rem ==========================================================
rem  Video Manager - stop the running service
rem  It simply calls stop.ps1 (same folder).
rem
rem  IMPORTANT: keep this file CRLF + pure ASCII.
rem  LF-only line endings break cmd parsing (window closes at once).
rem ==========================================================
cd /d "%~dp0"
title Video Manager - stop

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*

echo.
echo [Video Manager] done. Press any key to close this window.
pause
