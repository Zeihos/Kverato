@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -m venv .venv312
) else (
    python -m venv .venv312
)
if errorlevel 1 (
    echo [ERROR] Install Python 3.12 from python.org.
    pause
    exit /b 1
)
".venv312\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (pause & exit /b 1)
".venv312\Scripts\python.exe" -m pip install -r requirements-tg.txt
if errorlevel 1 (pause & exit /b 1)
if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo [SETUP] Fill TELEGRAM_TOKEN and TELEGRAM_OWNER_IDS in .env!
)
echo [KVERATO] Telegram dependencies installed.
pause
