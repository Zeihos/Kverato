@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto :folder_error
if exist ".venv\Scripts\python.exe" goto :venv
where py >nul 2>nul
if not errorlevel 1 goto :launcher
where python >nul 2>nul
if not errorlevel 1 goto :python

echo [ERROR] Python was not found. Run install_discord_windows.bat first.
goto :failed

:venv
".venv\Scripts\python.exe" "scripts\supervise_discord.py"
goto :done

:launcher
py -3 "scripts\supervise_discord.py"
goto :done

:python
python "scripts\supervise_discord.py"
goto :done

:done
set "BOT_EXIT=%errorlevel%"
if "%BOT_EXIT%"=="0" exit /b 0
echo.
echo [ERROR] KVERATO Discord supervisor stopped with exit code %BOT_EXIT%.
pause
exit /b %BOT_EXIT%

:folder_error
echo [ERROR] Cannot enter bot directory.
:failed
pause
exit /b 1
