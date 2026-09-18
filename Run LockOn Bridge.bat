@echo off
title LockOn Bridge (manual session)
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher "py" not found. Prefer "Install LockOn Bridge.bat" instead.
  pause
  exit /b 1
)
py -3 -m pip install -r "%~dp0requirements.txt" -q
echo Manual session mode — stays up until you close this window.
echo For normal use, run "Install LockOn Bridge.bat" once.
py -3 -m lockon_bridge --session
pause
