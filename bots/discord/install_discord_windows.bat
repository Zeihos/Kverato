@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto :folder_error

if exist ".venv\Scripts\python.exe" goto :dependencies
where py >nul 2>nul
if not errorlevel 1 goto :try_launcher
where python >nul 2>nul
if not errorlevel 1 goto :try_python

echo [ERROR] Python not found. Install Python 3.12 and try again.
goto :failed

:try_launcher
py -3.12 -m venv ".venv"
if not errorlevel 1 goto :dependencies
py -3 -m venv ".venv"
if not errorlevel 1 goto :dependencies
echo [ERROR] Cannot create .venv with the Python launcher.
goto :failed

:try_python
python -m venv ".venv"
if errorlevel 1 goto :venv_error
goto :dependencies

:venv_error
echo [ERROR] Cannot create .venv using Python.
goto :failed

:dependencies
if not exist ".venv\Scripts\python.exe" goto :venv_error
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :pip_error
".venv\Scripts\python.exe" -m pip install -r "requirements-discord.txt"
if errorlevel 1 goto :pip_error
if not exist ".env" copy /y ".env.example" ".env" >nul
if not exist "data" mkdir "data"
echo.
echo [OK] Discord bot dependencies installed.
echo [INFO] Open .env and set DISCORD_TOKEN and DISCORD_OWNER_IDS.
pause
exit /b 0

:folder_error
echo [ERROR] Cannot enter the bot directory.
goto :failed

:pip_error
echo [ERROR] Dependency installation failed. Read the errors above.
goto :failed

:failed
echo [ERROR] Installation did not complete.
pause
exit /b 1
