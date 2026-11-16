@echo off
setlocal
cd /d "%~dp0"
if not exist "requirements.txt" goto failed
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Run run_dashboard.bat or run_scraper.bat.
echo PostgreSQL is optional. Configure DATABASE_URL only if using database features.
pause
exit /b 0
:failed
echo Setup failed. Check the error above and ensure Python 3.10+ is installed.
pause
exit /b 1
