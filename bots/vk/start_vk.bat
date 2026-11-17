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
echo [KVERATO] Starting VK bot v1.0 (glass image cards) (Python 3.12 + Windows certificates)...
".venv312\Scripts\python.exe" "main.py" vk
set "EXIT_CODE=%ERRORLEVEL%"
echo [KVERATO] Bot stopped with code %EXIT_CODE%.
echo [SECURITY] Never share .env or raw tracebacks containing tokens.
pause
exit /b %EXIT_CODE%
