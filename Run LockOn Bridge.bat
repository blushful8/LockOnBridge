@echo off
title LockOn Bridge
cd /d "%~dp0"
if exist "%~dp0dist\LockOnBridge\LockOnBridge.exe" (
  start "" "%~dp0dist\LockOnBridge\LockOnBridge.exe"
  exit /b 0
)
where py >nul 2>&1
if errorlevel 1 (
  echo Build LockOnBridge first: run "Build LockOn Bridge.bat"
  echo Or install Python 3 and use: py -3 -m lockon_bridge --ui
  pause
  exit /b 1
)
py -3 -m pip install -r "%~dp0requirements.txt" -q
py -3 -m lockon_bridge --ui
