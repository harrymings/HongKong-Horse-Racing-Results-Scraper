# HK-Race-Results-Scraper
A Python application for scraping Hong Kong horse-racing results from the HKJC website:
<https://racing.hkjc.com/en-us/local/information/localresults>.

This repository now contains: scraper, incremental checkpointing, entity-resolution helpers, a PostgreSQL-backed schema, a FastAPI microservice, a small feature-engineering example, and baseline model training scaffolding.

## Dashboard preview

<img src="docs/dashboard_current.jpg" width="360" alt="Current dashboard in a narrow browser: export form, job quality counts and saved exports" />

Captured from the local dashboard in a narrow browser on 2026-11-16. The saved export is the single-race
live smoke sample, not a complete meeting or historical archive.

## Usage

Use source data only as permitted by HKJC and HKO terms. This project does not
bypass access restrictions or provide a verified pre-race prediction service.

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

Weather is joined by meeting date from two HKO sources. The `Venue*` columns come from the HKO station at the racecourse itself (SHA = Sha Tin, HPV = Happy Valley): daily mean, maximum and minimum temperature from independent climate datasets, with the Weather and Radiation Level bulletin supplementing missing maximum/minimum values where available. The `Weather*` columns are the Hong Kong Observatory headquarters daily extract (mean pressure, temperatures, dew point, humidity, cloud, rainfall) kept as a city-wide reference. Both are whole-day values for the meeting date at their station, not race-time readings; dates not yet published remain blank. Do not use full-day weather summaries or post-race odds as pre-race prediction features: they are not available at the time of betting.

## Ingesting scraped data into PostgreSQL
1. Configure your Postgres connection in `.env`: set `DATABASE_URL` to a valid `postgresql+psycopg2://user:pass@host:port/dbname` string.
2. The API is optional and can be started manually with `.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000`. Database-backed API features require a reachable PostgreSQL server.

To run ingestion manually:

```powershell
call .venv\Scripts\activate.bat
python check_db.py   # verifies DB connectivity
python -c "from ingest import ensure_schema; ensure_schema()" # new databases only
# Verify a backup/restore before applying migrations to an existing database.
python migrate.py --backup-verified
python ingest.py
python validate_racing_data.py --database
```

If ingestion fails, check that Postgres is running and `.env` is configured correctly.

## Features and predictions
After scraping enough completed races, build a baseline model with:

```powershell
python models_baseline.py
```

The baseline uses historical starts and wins only, with chronological holdouts.
It requires at least five meeting dates and writes a retrospective evaluation.
`POST /predict` returns HTTP 409 until independent pre-race entries and publication
cutoffs are implemented. Completed-race entrants are not future race cards.

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



## Data-integrity fixes and validation

The scraper now reads race metadata from the requested race header and identifies
runner tables by column names. `Class 4 (Restricted)` and en dashes are supported.
Horse identity uses the HKJC code when present; the old name ID remains as an
alias. Existing race and runner IDs are retained. Name-only identities remain
explicitly unresolved rather than being joined to a coded horse by name.

Result JSON preserves raw margin, odds, finish-time and dividend strings. Lengths
are numeric lengths; named margins use conventional approximations (nose 0.05,
short head 0.10, head 0.20, neck 0.25). Finish times and segment times are seconds.
`---` is zero only for a winner. DNF, scratched and disqualified statuses are
separate from numeric placing. Unknown layouts fail parsing.

Sectionals must match the returned meeting date, venue, race container and horse
code. Each runner has `sectional_status` and retains source values. Segment sums
are checked against finish time with a default 0.10 second tolerance, configurable
through `IncrementalScraper(..., sectional_tolerance=...)`. Invalid sectionals are
retained with an unverified status. The source URL remains attached to the race.

`scheduled_start_at_hkt` and `actual_start_at_hkt` remain null until a
suitable source is implemented. `timezone` is `Asia/Hong_Kong`. HKJC's elapsed
`Time:` values are never interpreted as wall-clock start times. The legacy SQL
`race_time` column remains untouched.

`weather` and `venue_weather` are **daily summaries**, not race-time observations.
CSV includes `WeatherResolution` and `VenueWeatherResolution`. Racecourse weather
has field-level dataset, station, observation date, source URL and availability.
CLMTEMP, CLMMAXT and CLMMINT are fetched independently; absent RYES does not block
historical climate values. RYES is not requested before 2019-09-10. Supported data
are determined by the actual station/date/field response; no nearby station is
silently substituted. Daily `known_at` remains null and
`is_pre_race_available=false`. Missing hourly observations are never fabricated.

Scraping and backfill share weather enrichment. Raw records and exports are saved
atomically. Missing refresh values retain existing values and weather provenance.
A smaller refreshed roster retains historical runners and is flagged as a blocking
validation issue pending reconciliation. A verified correction can replace an
existing value; an absent value cannot delete it.

### Safe local workflow

Run the inventory on the installation containing your history, before migration:

```bash
python inventory_backup.py --report output/inventory.json
python inventory_backup.py --database --backup-dir backups/BEFORE_MIGRATION --report output/backup_inventory.json
```

The second command requires PostgreSQL `pg_dump` and `pg_restore`. It copies and
checksums source files and verifies the dump archive can be listed. Test restoration
on a separate database before production migration; archive verification is not a
restoration test. Backups refuse to overwrite an existing directory. No historical
data was present in the development checkout.

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m compileall -q scraper_incremental.py dashboard.py ingest.py api.py
ruff check --select E9,F63,F7,F82 .
python validate_racing_data.py --data-dir data --report-dir output
```

Validation is read-only and reports blocking errors separately from warnings. It
returns 0 for checked records without blocking errors, 1 for errors, and 2 when
there is no data. A passing record check does not establish full meeting coverage.
Reports explicitly distinguish unverified coverage and unchecked DB parity.

After verifying backups, apply the additive migration and ingest:

```bash
python migrate.py --backup-verified
python ingest.py
python validate_racing_data.py --database --data-dir data --report-dir output
```

Existing installations already have `schema.sql` applied. For a brand-new database,
run `python -c "from ingest import ensure_schema; ensure_schema()"` before migration.
Migrations are versioned and transactional; they do not delete rows or collapse
legacy duplicates. Unique constraints stop migration when legacy duplicates need
review. Ingestion refuses duplicated race inputs and runner numbers. It stores
sectionals, dividends and weather with source JSON and does not synthesize odds
history from final odds. `horse_identity_aliases` can contain several coded horses
for an ambiguous legacy name; it must not be used as an unconditional one-to-one join.

To propose identity corrections without modifying records:

```bash
python reconcile_identity.py
python reconcile_identity.py --apply --backup-dir backups/BEFORE_IDENTITY_CHANGE
```

The apply command backs up the affected files and mappings before writing. It
changes horse IDs only where an official code is present in the record/link,
retains `legacy_horse_id`, and never changes race/runner IDs. Re-ingest afterward.
Name-only records and historical alias conflicts require review. For a full
installation backup, use the inventory command first.

Opt-in network workflows (respect source access policies):

```bash
python scraper_incremental.py --date 2026-11-14 --validate
python scraper_incremental.py --date 2026-11-14 --retry-failed
python scraper_incremental.py --date 2026-11-14 --refresh
python backfill_venue_weather.py
```

The scraper enumerates explicit official links for the requested date. It no longer
fills gaps by guessing all numbers up to a maximum. It does not yet independently
verify race-card declarations or distinguish every cancelled/unpublished layout;
an unknown page is a parse failure, never assumed unpublished. CLI checkpoints
record per-race fetch status/checksum. Older-than-seven-day races with dividends,
valid sectionals and clean metadata are cached for 30 days after a successful
fetch. Incomplete races are revisited; `--retry-failed` skips only verified successes;
`--refresh` forces re-fetching. The dashboard always refreshes selected results.

Dashboard states are `SUCCESS`, `PARTIAL`, `FAILED`, `CANCELLED`, with expected
**discovered** race counts and per-race errors. A failed race cannot yield a clean
success. Each export has a `.manifest.json` with collection status, validation,
coverage limitations, and quality counts. Backfill preserves earlier collection
metadata; stand-alone exports without a collection manifest are `UNVERIFIED`.
Job progress is persisted so interrupted work is marked failed after restart.
Only one scrape runs at a time; stop requests wait for the active request to finish.
Use the default localhost binding. Remote POSTs require `DASHBOARD_TOKEN` through
`X-Dashboard-Token`; the interactive dashboard is intended for local use.
Deletion is limited to `hkjc_*.csv/json` exports, excludes symlinks, and uses the
existing browser confirmation.

### Model limits

`/predict` retains its route but returns HTTP 409 until independent declared
runners and feature publication cutoffs are implemented. Completed-race entrants
must not be represented as future entries. Offline baseline training uses only
historical start/win counts, excludes same-day starts from history, splits by
meeting date into train/validation/test, and fits imputation on training data only.
The evaluation JSON includes ROC-AUC, PR-AUC, log loss, Brier score, calibration and
top-pick hit rate. At least five meeting dates are required. This remains a
retrospective diagnostic: historical result publication cutoffs and the
completed-race cohort have not been verified for a pre-race backtest. There is no
profitability claim.

See [validation evidence and remaining work](docs/VALIDATION.md). CI runs offline
fixtures and an isolated PostgreSQL test; CI never scrapes external sources.
