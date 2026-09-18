@echo off
title LockOn Bridge
cd /d "%~dp0"
if exist "%~dp0dist\LockOnBridge\LockOnBridge.exe" (
  start "" "%~dp0dist\LockOnBridge\LockOnBridge.exe"
  exit /b 0
)
where py >nul 2>&1
if errorlevel 1 (
  echo.
  echo LockOnBridge build not found and Python is missing.
  echo 1^) Run "Build LockOn Bridge.bat" on a PC with Python, or
  echo 2^) Download LockOnBridge.zip from GitHub Releases and extract it.
  echo.
  pause
  exit /b 1
)
py -3 -m pip install -r "%~dp0requirements.txt" -q
echo Opening LockOn Bridge control window...
echo Turn ON "Bridge enabled" to start; leave OFF for zero PC load.
py -3 -m lockon_bridge --ui
