@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
if not exist ".venv312\Scripts\python.exe" (
  echo [ERROR] Run install_vk_windows.bat first.
  pause
  exit /b 1
)
if not exist ".env" (
  echo [ERROR] Configure .env with VK_TOKEN first.
  pause
  exit /b 1
)
echo [KVERATO] v1.0 VK supervised mode. Ctrl+C for stop.
".venv312\Scripts\python.exe" scripts\supervise_vk.py
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [ERROR] Watchdog stopped after repeated errors. Inspect the console output.
pause
exit /b %RC%
