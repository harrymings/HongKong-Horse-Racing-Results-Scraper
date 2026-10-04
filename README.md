# HK-Race-Results-Scraper
A Python application for scraping Hong Kong horse-racing results from the HKJC website:
<https://racing.hkjc.com/en-us/local/information/localresults>.

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

2. Start the local dashboard:

```powershell
.\run_dashboard.bat
# then open http://127.0.0.1:8501
```

3. Or run the command-line scraper. It uses the current HKJC results listing by default; `SCRAPER_BASE_URL` can override it in `.env`:

```powershell
.\run_scraper.bat
```

## Dashboard (recommended)

A Flask dashboard is included to control scraping, preview CSV results, and download CSV or JSON exports.

1. Start the dashboard launcher after setup:

```powershell
.\run_dashboard.bat
```

Or start it manually:

```powershell
python dashboard.py
# Open http://127.0.0.1:8501 in your browser
```

Choose a date range, Sha Tin and/or Happy Valley, CSV and/or JSON, then combined or per-day files. The main CSV contains the race, runner, sectionals, dividends, final odds, and date-matched historical weather fields (racecourse station + HKO Observatory) in one file. Sectionals and dividends are stored as JSON text in the relevant CSV cells so each runner remains one row. JSON exports retain the full nested structure. Exports are saved under `output/`; the command-line scraper also writes race JSON under `data/`.

Weather is joined by meeting date from two HKO sources. The `Venue*` columns come from the HKO station at the racecourse itself (SHA = Sha Tin, HPV = Happy Valley): daily maximum/minimum temperature from the Weather and Radiation Level bulletin (published ~01:30 the next day, so available to yesterday) and daily mean temperature from the climate dataset (loads with a ~1-month lag). The `Weather*` columns are the Hong Kong Observatory headquarters daily extract (mean pressure, temperatures, dew point, humidity, cloud, rainfall) kept as a city-wide reference. Both are whole-day values for the meeting date at their station, not race-time readings; dates not yet published remain blank. Do not use full-day weather summaries or post-race odds as pre-race prediction features: they are not available at the time of betting.

## Ingesting scraped data into PostgreSQL
1. Configure your Postgres connection in `.env`: set `DATABASE_URL` to a valid `postgresql+psycopg2://user:pass@host:port/dbname` string.
2. The API is optional and can be started manually with `.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000`. Database-backed API features require a reachable PostgreSQL server.

To run ingestion manually:

```powershell
call .venv\Scripts\activate.bat
python check_db.py   # verifies DB connectivity
python ingest.py     # ingests all JSON files from data/ into Postgres
```

If ingestion fails, check that Postgres is running and `.env` is configured correctly.

## Features and predictions
After scraping enough completed races, build a baseline model with:

```powershell
python models_baseline.py
```

The model uses historical starts, wins, draw, weights and race distance. It excludes the current result, finishing time, margin and odds from features. `POST /predict` requires a saved `model.pkl`, an initialized database and runners for the requested race. Predictions are only useful before a race if pre-race entries are available; the current results scraper discovers runners from completed race pages.

## Data to scrape next
- Pre-race race cards: declared runners, draw, carried weight, jockey, trainer, equipment and official ratings. This is the key addition needed for genuine pre-race predictions.
- Horse, jockey and trainer profiles: age/sex/pedigree and historical performance, with stable source IDs and careful entity matching.
- Sectional times and running positions are included from HKJC's sectional-time pages, including per-section positions and margins behind the leader.
- Race incidents and veterinary/steward notes: available from the race reports and helpful for explaining unusual outcomes.
- Dividends and final win odds are included from the result pages. Initial odds are not present in the verified results source and still need a historical odds feed.
- Track surface/rail details are included where present on results. Historical weather is matched by date from HKO observations, including the station at each racecourse (daily max/min/mean temperature); race-time weather is not published historically by HKO for these stations.
- Gear and jockey claim allowance are race-card fields not present in the verified historical result rows; they remain unavailable until a reliable race-card source is added.

Keep requests rate-limited and respect HKJC terms and access restrictions. The scraper does not bypass authentication or anti-bot controls.

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


