@echo off
rem ==========================================================
rem  Video Manager - Windows one-click launcher
rem  It simply calls run.ps1 (same folder), which does the real
rem  work and prints all messages.
rem
rem  IMPORTANT: keep this file CRLF + pure ASCII.
rem  LF-only line endings break cmd parsing (window closes at once),
rem  and non-ASCII text can break under a non-matching code page.
rem ==========================================================
cd /d "%~dp0"
title Video Manager

echo [Video Manager] starting ...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*

echo.
echo [Video Manager] service stopped. Press any key to close this window.
pause
