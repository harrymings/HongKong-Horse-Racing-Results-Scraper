# HK-Race-Results-Scraper
A Python script that automatically scrapes horse racing results from the Hong kong Jockey Club (HKJC) website 
<br><a>https://racing.hkjc.com/racing/information/English/racing/LocalResults.aspx</a>.

This repository now contains: scraper, incremental checkpointing, entity-resolution helpers, a PostgreSQL-backed schema, a FastAPI microservice, a small feature-engineering example, and baseline model training scaffolding.

# Disclaimer
This project is for educational purposes only and is not intended for commerical use. The author shall not be held liable for any claims, damages, or other liabilities arising from or in connection with the script or its use. 

[![Anurag's GitHub stats](https://github-readme-stats.vercel.app/api?username=harrymings)](https://github.com/anuraghazra/github-readme-stats)

# Reminder
Please install all necessary python packages by running
pip install -r requirements.txt

# Sample 
<img width="2373" height="899" alt="Sample_data" src="https://github.com/user-attachments/assets/b920b0a8-51de-4ea9-86d5-89350dded08c" />

## Quickstart (Windows)
1. Run one-click setup to create a virtualenv and install deps:

```powershell
.\run_setup.bat
```

2. Start the API:

```powershell
.\run_api.bat
# then open http://127.0.0.1:8000/health
```

3. Run the scraper (after configuring `scraper_incremental.base_url` to a real HKJC listing page):

```powershell
.\run_scraper.bat
```

## Dashboard (recommended)

A small Flask dashboard is included to control scraping, preview results, and download CSVs.

1. Activate your virtual environment and install requirements if needed:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run the dashboard:

```powershell
python dashboard.py
# Open http://127.0.0.1:8501 in your browser
```

Notes:
- By default CSV outputs are saved under the `output/` folder in the repository root.
- The dashboard runs the scraper in a background thread, shows a progress bar and live log, and provides a combined CSV download when the job finishes.

## Ingesting scraped data into PostgreSQL
1. Configure your Postgres connection in `.env`: set `DATABASE_URL` to a valid `postgresql+psycopg2://user:pass@host:port/dbname` string.
2. The API batch will attempt to connect and automatically run an ingestion step before starting the server. If the DB is not reachable the API will still start but without data.

To run ingestion manually:

```powershell
call .venv\Scripts\activate.bat
python check_db.py   # verifies DB connectivity
python ingest.py     # ingests all JSON files from data/ into Postgres
```

If ingestion fails, check that Postgres is running and `.env` is configured correctly.

## Notes & next steps
- The scraper currently saves raw race JSON files in `data/` and maintains `mappings/entity_mappings.json` for canonical IDs.
- The ingestion process is idempotent and uses `ON CONFLICT DO NOTHING` to avoid duplicates.
- This repo is an evolving prototype — the scraping selectors, data model, and feature pipeline need further refinement for production use.

## Files not to commit
Do not commit the following to GitHub — they are generated, environment-specific, or contain sensitive data:

- `.venv/` — local virtual environment
- `data/` — scraped JSON/DB files
- `mappings/` — entity mapping store (may contain PII or local IDs)
- `checkpoint.json` — scraper state
- `model.pkl` — trained model binaries
- `.env` or any file containing credentials

These paths are included in `.gitignore`.

## Database
This project expects a PostgreSQL database. Set `DATABASE_URL` environment variable before running the API or `db.init_db()`.


