@echo off
REM One-click setup: create virtual environment and install requirements
if not exist ".venv\Scripts\activate.bat" (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
REM Use the venv python explicitly to avoid global pip issues
set VENV_PY=.venv\Scripts\python.exe
%VENV_PY% -m pip install --upgrade pip
if exist requirements.txt (
    %VENV_PY% -m pip install -r requirements.txt || (
        echo "pip install -r requirements.txt failed, attempting fallback installs"
        %VENV_PY% -m pip install httpx requests beautifulsoup4 lxml psycopg2-binary SQLAlchemy python-dotenv || (
            echo "Fallback install also failed; please inspect the error above"
        )
    )
) else (
    echo requirements.txt not found; installing essential packages
    %VENV_PY% -m pip install httpx requests beautifulsoup4 lxml psycopg2-binary SQLAlchemy python-dotenv
)
echo.
REM Create .env with placeholder DATABASE_URL if missing
if not exist ".env" (
    echo DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/hkracing> .env
    echo SCRAPER_BASE_URL=https://racing.hkjc.com/racing/information/English/racing/LocalResults.aspx>> .env
    echo Created .env with placeholder DATABASE_URL
    echo Please edit .env with your Postgres credentials
)
echo.
echo Setup complete.
echo Edit .env then run run_api.bat or run_scraper.bat
pause
