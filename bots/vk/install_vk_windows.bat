@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto :fail
where py >nul 2>nul
if errorlevel 1 goto :python
py -3.12 --version >nul 2>nul
if errorlevel 1 goto :python
if not exist ".env" copy /Y ".env.example" ".env" >nul
if not exist ".venv312\Scripts\python.exe" (
    echo [KVERATO] Creating Python 3.12 environment in .venv312...
    py -3.12 -m venv ".venv312"
    if errorlevel 1 goto :fail
)
".venv312\Scripts\python.exe" -c "import sys; assert sys.version_info[:2] == (3, 12)"
if errorlevel 1 goto :fail
".venv312\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto :fail
".venv312\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv312\Scripts\python.exe" -m pip install -r requirements-vk.txt
if errorlevel 1 goto :fail
".venv312\Scripts\python.exe" -c "import dotenv, vkbottle, loguru, truststore, PIL, aiohttp; from vkbottle import PhotoMessageUploader, DocMessagesUploader; print('KVERATO: dependencies and uploaders OK')"
if errorlevel 1 goto :fail
echo [OK] Dependencies installed. Configure VK_TOKEN in .env, run start_vk.bat
pause
exit /b 0
:python
echo [ERROR] Python 3.12 not installed. Run in PowerShell: py install 3.12
pause
exit /b 1
:fail
echo [ERROR] Installation failed. Read the message above.
pause
exit /b 1
