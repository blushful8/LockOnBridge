@echo off
title LockOn Bridge
cd /d "%~dp0"
REM Prefer the built exe; otherwise open the Python control UI.
if exist "%~dp0dist\LockOnBridge.exe" (
  start "" "%~dp0dist\LockOnBridge.exe"
  exit /b 0
)
where py >nul 2>&1
if errorlevel 1 (
  echo.
  echo LockOnBridge.exe not found and Python is missing.
  echo 1^) Run "Build LockOn Bridge.bat" on a PC with Python, or
  echo 2^) Download LockOnBridge.exe from GitHub Releases.
  echo.
  pause
  exit /b 1
)
py -3 -m pip install -r "%~dp0requirements.txt" -q
echo Opening LockOn Bridge control window…
echo Turn ON "Bridge enabled" to start; leave OFF for zero PC load.
py -3 -m lockon_bridge --ui
