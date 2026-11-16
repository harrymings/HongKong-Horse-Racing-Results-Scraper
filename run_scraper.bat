@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run run_setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scraper_incremental.py %*
set "RUN_EXIT=%ERRORLEVEL%"
pause
exit /b %RUN_EXIT%
