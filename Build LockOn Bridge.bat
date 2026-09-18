@echo off
title Build LockOn Bridge.exe
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1"
echo.
pause
