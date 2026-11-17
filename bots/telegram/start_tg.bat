@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv312\Scripts\python.exe" (
    echo [ERROR] Run install_tg_windows.bat first.
    pause
    exit /b 2
)
".venv312\Scripts\python.exe" main.py
if errorlevel 1 pause
