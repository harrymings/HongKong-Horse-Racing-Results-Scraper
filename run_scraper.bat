@echo off
REM Activate environment and run the incremental scraper
if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found. Run run_setup.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python scraper_incremental.py
pause
