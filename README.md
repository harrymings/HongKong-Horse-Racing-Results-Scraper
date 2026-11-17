# HK-Race-Results-Scraper
A Python application for scraping Hong Kong horse-racing results from the HKJC website:
<https://racing.hkjc.com/en-us/local/information/localresults>.

This repository now contains: scraper, incremental checkpointing, entity-resolution helpers, a PostgreSQL-backed schema, a FastAPI microservice, a small feature-engineering example, and baseline model training scaffolding.

## Desktop demo: real scrape job

![Desktop dashboard after a completed scrape job](docs/dashboard_desktop_job.jpg)

Real dashboard job run on 2026-11-16 for Sha Tin, 2026-11-14: **11/11 discovered
race pages collected, 140 runner records, CSV and JSON exports generated**.
Race 6 was officially abandoned and is preserved with its status and refund
dividends; the ten other races have result rosters. Withdrawn horses remain
explicitly marked, including a replacement without a published horse number.
Collection coverage refers to the official linked pages, not an independent
race-card audit.

![Desktop saved exports and preview of the scraped records](docs/dashboard_desktop_preview.jpg)

The table keeps readable column widths and scrolls horizontally on small screens.
The screenshots use a 1440 px desktop viewport and real job status and exports.

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
- Track surface/rail details are included where present on results. Historical weather is matched by date from HKO observations, including the station at each racecourse (daily max/min/mean temperature); timestamped HKO observations are retrieved from the public DATA.GOV.HK archive where station/date coverage exists (see race-time weather below).
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

`scheduled_start_at_hkt` is populated from the exact HKJC declared-starters
meeting/race header, with `Asia/Hong_Kong` timezone and source URL.
`actual_start_at_hkt` remains null unless independently supplied by a reliable
source. The scheduled time is never called the actual off-time. HKJC's elapsed
`Time:` values are never used as wall-clock starts. Legacy SQL `race_time` remains
untouched.

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

## Race-time weather (hourly and subhourly source intervals)

New scrapes now attempt **real timestamped observations**, separate from the
existing daily weather. There is no daily-to-hourly interpolation. The source
interval is kept per field: temperature/humidity are 1-minute means, wind is a
10-minute mean with gust, and rainfall is a past-hour total only when its archive
is available. These are not fabricated full-hour temperature averages.

The provider uses the [official DATA.GOV.HK archive API](https://data.gov.hk/en/help/api-spec)
and exact HKO station names (Sha Tin or Happy Valley). It selects the latest
archive capture available before the scheduled start, then verifies the actual
observation time is no later than that start and no more than 30 minutes old.
Archive capture precision is one minute, so availability uses a conservative
end-of-minute upper bound. Actual start is used only if separately known.

CSV adds `RaceTimeWeatherStatus`, `RaceTimeTempC`, `RaceTimeHumidityPct`,
`RaceTimeWindSpeedKph`, `RaceTimeWindDirection`, `RaceTimeWindGustKph`,
`RaceTimeRainfallMm`, target kind/time and field-level provenance JSON. JSON adds
`race_time_weather.fields`, each with observation interval, raw value, station,
original/archived source URL, archive capture, availability bound and age.
`RaceTimeWeatherResolution=mixed` means fields have different source intervals;
it is not an hourly mean. Daily columns retain their original meaning.

**Coverage limits:** sampled 2018-11-14 files had no archive; 2022-11-14 and
2026-11-14 had temperature at both venues, humidity/wind at Sha Tin, and no
Happy Valley humidity/wind station in those files. Historical rainfall's tested
archive endpoint returned 404, so rainfall stays null with `ARCHIVE_UNAVAILABLE`.
These are sampled dates, not a guarantee for every intervening date. The provider
never substitutes a neighbouring station. Source records are provisional.

For command-line scraping, add `--race-time-weather` (also supported by
`run_scraper.bat`); dashboard jobs enable it automatically:

```bash
python scraper_incremental.py --date 2026-11-14 --race-time-weather --validate
```

For an existing dataset, preview first; applying requires a new verified JSON/export
backup directory and regenerates matching existing exports:

```bash
python backfill_race_time_weather.py --date 2026-11-14
python backfill_race_time_weather.py --date 2026-11-14 --apply --backup-dir backups/BEFORE_TIMED_WEATHER
python audit_weather_coverage.py --dates 2018-11-14 2022-11-14 2026-11-14
```

Use `--max-age-minutes` to change the 30-minute maximum (up to 120). Full dry-run
reports are in `output/race_time_weather_report.json`; coverage reports are in
`output/weather_coverage.json`. A dated `weather_cache/` stores source files and
valid misses; transport/schema failures are retried. Historic schedules are
refreshed after the cache expires, while failed refreshes retain the last known
schedule with an explicit status. Do not manually edit schedules without source
evidence. Timed weather is replaced as a complete match if the target changes,
so an older target's values cannot leak into a new target.

Before DB ingestion, apply migration `002_race_time_weather.sql` with the same
backup-verification workflow. Timed fields are stored individually in
`weather_observations` with their own interval and timestamp, joined to the race's
specific target. No production DB migration is automatic.

This does **not** enable pre-race prediction. The currently accessible historical
race card does not establish its original publication cutoff; `pre_race_verified`
remains false and `/predict` remains guarded. See [source audit and validation](docs/RACE_TIME_WEATHER.md).

Weather attribution: Hong Kong Observatory, HKSAR Government and DATA.GOV.HK.
Use is subject to [DATA.GOV.HK terms](https://data.gov.hk/en/terms-and-conditions)
and the source's terms. No paid service, NOAA station substitution, forecast or
reanalysis is used.
