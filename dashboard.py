from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
import json
import os
import re
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd
import time
import threading
import hmac
import httpx
from urllib.parse import parse_qs, urlparse
from scraper_incremental import DATA_DIR, DEFAULT_BASE_URL, IncrementalScraper, RaceNotPublished, RaceParseError
from persistence import atomic_json, atomic_text
from weather import fetch_venue_weather, enrich_race_weather

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)

BASE_URL_TEMPLATE = DEFAULT_BASE_URL + "?racedate={date}"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
COURSES = {"ST": "Sha Tin", "HV": "Happy Valley"}
PREVIEW_COLUMNS = ["MeetDate", "RaceCourse", "RaceNumber", "HorseName", "Placing", "FinalWinOdds", "VenueWeatherStation", "VenueMaxTempC", "VenueMinTempC", "VenueMeanTempC", "WeatherMeanTempC", "WeatherRainfallMm", "WeatherMeanCloudPct", "HorseNo", "JockeyName", "TrainerName", "Draw", "FinishTime", "TrackType", "RailPosition", "VenueWeatherStatus", "WeatherStatus"]
HKO_DAILY_ARCHIVE = "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{year}{month:02d}.xml"
HKO_OPEN_DATA = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
# Stations located AT the racecourses (HKO Open Data API station codes).
VENUE_STATIONS = {"Sha Tin": "SHA", "Happy Valley": "HPV"}


job_lock = threading.Lock()
cancel_event = threading.Event()

# Job status is persisted after completion; interrupted jobs are classified on restart.
job = {
    'running': False,
    'state': 'IDLE', 'races': [], 'expected': 0, 'successful': 0,
    'total': 0,
    'done': 0,
    'current': '',
    'log': [],
    'output_file': None,
    'downloads': [],
    'error': None,
}

JOB_FILE = OUTPUT_DIR / 'job_status.json'
if JOB_FILE.exists():
    try:
        job.update(json.loads(JOB_FILE.read_text(encoding='utf-8')))
        if job.get('running'):
            job.update(running=False, state='FAILED', error='Process interrupted; retry failed races')
    except (ValueError, OSError):
        pass


def _job_state():
    if cancel_event.is_set():
        return 'CANCELLED'
    if job.get('error'):
        return 'PARTIAL' if job.get('successful') else 'FAILED'
    unsuccessful = any(r['status'] != 'SUCCESS' for r in job.get('races', []))
    if unsuccessful:
        return 'PARTIAL' if job.get('successful') else 'FAILED'
    return 'SUCCESS' if job.get('expected') else 'FAILED'


def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)


def _course_from_url(race_url):
    query = parse_qs(urlparse(race_url).query)
    return next((values[0].upper() for key, values in query.items() if key.lower() == 'racecourse' and values), None)


def _race_rows(races):
    rows = []
    for race in races:
        weather = race.get("weather") or {}
        venue_weather = race.get("venue_weather") or {}
        for runner in race['runners']:
            rows.append({
                "MeetDate": race["meeting_date"],
                "RaceURL": race["source_url"],
                "RaceHeader": race["race_name"],
                "RaceDetails": race["grade"],
                "RaceGoing": race["going"],
                "RaceCourse": race["venue"],
                "CourseDescription": race.get("course_description"),
                "TrackType": race.get("track_type"),
                "RailPosition": race.get("rail_position"),
                "RaceNumber": race["race_number"],
                "DistanceM": race["distance_m"],
                "Placing": runner["finishing_pos_text"],
                "HorseNo": runner["horse_no"],
                "HorseName": runner["horse_name"],
                "HorseLink": runner["horse_url"],
                "JockeyName": runner["jockey_name"],
                "JockeyLink": runner["jockey_url"],
                "TrainerName": runner["trainer_name"],
                "TrainerLink": runner["trainer_url"],
                "ActualWt": runner["actual_weight"],
                "DeclaredHorseWt": runner["declared_horse_weight"],
                "Draw": runner["draw"],
                "Margin": runner["margin_text"],
                "RunningPosition": runner["running_position"],
                "FinishTime": runner["finishing_time_text"],
                "WinOdds": runner["win_odds"],
                "FinalWinOdds": runner.get("final_win_odds"),
                "InitialWinOdds": runner.get("initial_win_odds"),
                "Gear": runner.get("gear"),
                "JockeyAllowance": runner.get("jockey_allowance"),
                "Sectionals": json.dumps(runner.get("sectionals", []), ensure_ascii=False),
                "Dividends": json.dumps(race.get("dividends", []), ensure_ascii=False),
                "WeatherDate": weather.get("date"),
                "WeatherStation": weather.get("station"),
                "WeatherResolution": "Daily" if weather else None,
                "WeatherMeanPressureHpa": weather.get("mean_pressure_hpa"),
                "WeatherMaxTempC": weather.get("max_temp_c"),
                "WeatherMeanTempC": weather.get("mean_temp_c"),
                "WeatherMinTempC": weather.get("min_temp_c"),
                "WeatherMeanDewPointC": weather.get("mean_dew_point_c"),
                "WeatherMeanHumidityPct": weather.get("mean_humidity_pct"),
                "WeatherMeanCloudPct": weather.get("mean_cloud_pct"),
                "WeatherRainfallMm": weather.get("rainfall_mm"),
                "WeatherRainfallTrace": weather.get("rainfall_trace"),
                "WeatherSource": weather.get("source_url"),
                "WeatherStatus": "Matched by meeting date; HKO station, not venue or race-time conditions" if weather else "No historical daily observation available",
                "VenueWeatherStation": venue_weather.get("station"),
                "VenueWeatherDate": venue_weather.get("date"),
                "VenueMaxTempC": venue_weather.get("max_temp_c"),
                "VenueMinTempC": venue_weather.get("min_temp_c"),
                "VenueMeanTempC": venue_weather.get("mean_temp_c"),
                "VenueWeatherSource": venue_weather.get("source_url"),
                "VenueWeatherStatus": ("Daily max/min measured at the " + venue_weather["station"] + " racecourse station; whole-day values, not race-time readings") if venue_weather else "No racecourse-station observation published yet",
                "HorseCode": runner.get("horse_code"),
                "FinishStatus": runner.get("finish_status"),
                "SectionalStatus": runner.get("sectional_status"),
                "VenueWeatherResolution": "Daily" if venue_weather else None,
                "ScheduledStartAtHKT": race.get("scheduled_start_at_hkt"),
                "ActualStartAtHKT": race.get("actual_start_at_hkt"),
                "Timezone": race.get("timezone", "Asia/Hong_Kong"),
                "HorseId": runner["horse_id"],
                "JockeyId": runner["jockey_id"],
                "TrainerId": runner["trainer_id"],
            })
    return rows


def _save_exports(races, file_stem, output_format, collection_summary=None):
    downloads = []
    if output_format in {"csv", "both"}:
        csv_name = f"{file_stem}.csv"
        atomic_text(OUTPUT_DIR / csv_name, pd.DataFrame(_race_rows(races)).to_csv(index=False), encoding='utf-8-sig')
        downloads.append(csv_name)
    if output_format in {"json", "both"}:
        json_name = f"{file_stem}.json"
        atomic_json(OUTPUT_DIR / json_name, races)
        downloads.append(json_name)
    from validate_racing_data import validate_races
    validation = validate_races(races)
    previous_path = OUTPUT_DIR / (file_stem + '.manifest.json')
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    summary = collection_summary or {}
    collection_status = _job_state() if collection_summary is not None else previous.get('collection_status', 'UNVERIFIED')
    if validation['errors']:
        collection_status = 'PARTIAL'
        if collection_summary is not None:
            job['error'] = 'Export contains blocking validation errors; see manifest'
    manifest = {'collection_status': collection_status,
                'expected': summary.get('expected', previous.get('expected')),
                'successful': summary.get('successful', previous.get('successful')),
                'race_diagnostics': list(summary.get('races', previous.get('race_diagnostics', []))),
                'race_count': len(races), 'runner_count': sum(len(r['runners']) for r in races),
                'coverage_basis': 'Official linked races only; independent declaration roster not verified',
                'validation_status': validation['status'], 'validation': validation, 'files': list(downloads),
                'sectional_valid_runners': sum(r.get('sectional_status') == 'VALID' for race in races for r in race['runners']),
                'venue_daily_weather_races': sum(bool(race.get('venue_weather')) for race in races)}
    atomic_json(previous_path, manifest)
    return downloads


def _weather_number(value):
    try:
        return float(value.strip())
    except (AttributeError, ValueError):
        return None


def _fetch_daily_weather(meeting_date, month_cache):
    month_key = (meeting_date.year, meeting_date.month)
    if month_key not in month_cache:
        source_url = HKO_DAILY_ARCHIVE.format(year=meeting_date.year, month=meeting_date.month)
        response = httpx.get(source_url, timeout=20, follow_redirects=True)
        if response.status_code == 404:
            month_cache[month_key] = {}
        else:
            response.raise_for_status()
            daily = {}
            if not response.text.lstrip().startswith('{'):
                raise ValueError('HKO daily archive payload is not the recorded JSON schema')
            payload = response.json()
            if not isinstance(payload.get('stn', {}).get('data'), list):
                raise ValueError('Unexpected HKO daily archive schema')
            for month in payload["stn"]["data"]:
                if int(month["month"]) != meeting_date.month:
                    continue
                for values in month["dayData"]:
                    if not str(values[0]).isdigit() or len(values) < 9:
                        continue
                    date_key = date(meeting_date.year, meeting_date.month, int(values[0])).isoformat()
                    daily[date_key] = {
                        "date": date_key,
                        "station": "Hong Kong Observatory",
                        "mean_pressure_hpa": _weather_number(values[1]),
                        "max_temp_c": _weather_number(values[2]),
                        "mean_temp_c": _weather_number(values[3]),
                        "min_temp_c": _weather_number(values[4]),
                        "mean_dew_point_c": _weather_number(values[5]),
                        "mean_humidity_pct": _weather_number(values[6]),
                        "mean_cloud_pct": _weather_number(values[7]),
                        "rainfall_mm": _weather_number(values[8]),
                        "rainfall_trace": str(values[8]).strip().lower() == "trace",
                        "source_url": source_url,
                    }
            month_cache[month_key] = daily
    return month_cache[month_key].get(meeting_date.isoformat())


_fetch_venue_weather = fetch_venue_weather


def scrape_date(scraper, meet_date_str, selected_courses):
    races = []
    iso_date = datetime.strptime(meet_date_str, "%d/%m/%Y").strftime("%Y/%m/%d")
    scraper.base_url = BASE_URL_TEMPLATE.format(date=iso_date)
    urls = scraper.list_recent_race_urls()
    requested_date = datetime.strptime(meet_date_str, "%d/%m/%Y").date().isoformat()
    from scraper_incremental import _race_identity
    urls = [u for u in urls if _race_identity(u)[1] == requested_date]
    selected = [u for u in urls if _course_from_url(u) in selected_courses]
    job['expected'] += len(selected)
    if not selected:
        job['error'] = f'No official race links discovered for {meet_date_str}; coverage unverified'
    for race_url in selected:
        if cancel_event.is_set():
            break
        diagnostic = {'url': race_url, 'status': 'ATTEMPTED', 'error': None}
        job['races'].append(diagnostic)
        try:
            race = scraper.parse_and_save(race_url)
        except RaceNotPublished as exc:
            diagnostic.update(status='NOT_PUBLISHED', error=str(exc))
            continue
        except Exception as exc:
            diagnostic.update(status='FAILED_HTTP' if not isinstance(exc, ValueError) else 'FAILED_PARSE', error=str(exc))
            job['log'].append(f"Failed {race_url}: {exc}")
            continue
        diagnostic['status'] = 'SUCCESS'
        job['successful'] += 1
        races.append(race)
        atomic_json(JOB_FILE, job)
        time.sleep(scraper.rate_limit)

    return races


def run_scrape_job(start_date, end_date, selected_courses, output_format='csv', output_mode='single'):
    job['running'] = True
    job.update(state='RUNNING', races=[], expected=0, successful=0)
    job['log'] = [f"Job started: {start_date} to {end_date}"]
    dates = [d for d in daterange(start_date, end_date)]
    job['total'] = len(dates)
    job['done'] = 0
    job['output_file'] = None
    job['downloads'] = []
    job['error'] = None
    atomic_json(JOB_FILE, job)
    races_by_date = []
    weather_months = {}
    ryes_cache = {}
    clm_months = {}
    matched_dates = set()
    missing_dates = set()
    scraper = None
    try:
        scraper = IncrementalScraper(DEFAULT_BASE_URL)
        for d in dates:
            if cancel_event.is_set():
                job['log'].append('Job cancelled')
                break
            meet_date = d.strftime('%d/%m/%Y')
            job['current'] = meet_date
            job['log'].append(f"Scraping {meet_date}")
            races = scrape_date(scraper, meet_date, selected_courses)
            if races:
                try:
                    weather = _fetch_daily_weather(d, weather_months)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    weather = None
                    job['log'].append(f"HKO archive unavailable for {meet_date}: {exc}")
                for i, race in enumerate(races):
                    races[i] = enrich_race_weather(
                        race, lambda day: weather,
                        lambda venue, day: _fetch_venue_weather(venue, day, ryes_cache, clm_months),
                        DATA_DIR / f"race_{race['race_id']}.json")
                (matched_dates if weather else missing_dates).add(d.isoformat())
                races_by_date.append((d, races))
                runner_count = sum(len(race['runners']) for race in races)
                job['log'].append(f"Collected {len(races)} races and {runner_count} runners")
                if output_mode == 'separate':
                    course_slug = "-".join(sorted(selected_courses)).lower()
                    stem = f"hkjc_{course_slug}_{d.strftime('%Y-%m-%d')}"
                    job['downloads'].extend(_save_exports(races, stem, output_format, job))
            else:
                job['log'].append(f"No published race results for {meet_date}")
            job['done'] += 1
            atomic_json(JOB_FILE, job)
            time.sleep(0.1)

        if races_by_date and output_mode == 'single':
            all_races = [race for _, races in races_by_date for race in races]
            course_slug = "-".join(sorted(selected_courses)).lower()
            stem = f"hkjc_{course_slug}_{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}"
            job['downloads'].extend(_save_exports(all_races, stem, output_format, job))
        if races_by_date:
            job['log'].append(f"Historical HKO daily weather matched {len(matched_dates)} meeting date(s); unavailable for {len(missing_dates)}")
            venue_count = sum(1 for _, rs in races_by_date for r in rs if r.get('venue_weather'))
            job['log'].append(f"Racecourse-station weather (Sha Tin/Happy Valley) attached to {venue_count} race(s)")
        if job['downloads']:
            job['output_file'] = job['downloads'][0]
            job['log'].append(f"Ready to download {len(job['downloads'])} file(s)")
        elif not races_by_date:
            job['log'].append("No files created because no published races matched the selection")
    except Exception as e:
        job['error'] = str(e)
        job['log'].append(f"Error: {e}")
    finally:
        if scraper:
            try:
                scraper.client.close()
            except Exception:
                pass
        job['state'] = _job_state()
        job['running'] = False
        job['current'] = ''
        job['log'].append('Job finished: ' + job['state'])
        atomic_json(JOB_FILE, job)


@app.before_request
def protect_mutations():
    if request.method != 'POST':
        return None
    origin=request.headers.get('Origin')
    if origin and urlparse(origin).netloc != request.host:
        return jsonify(error='Cross-origin mutation rejected'),403
    if request.remote_addr not in {'127.0.0.1','::1'}:
        configured=os.getenv('DASHBOARD_TOKEN')
        provided=request.headers.get('X-Dashboard-Token','')
        if not configured or not hmac.compare_digest(configured,provided):
            return jsonify(error='Remote mutations require X-Dashboard-Token'),403


@app.route('/')
def index():
    meeting_dates = []
    for path in DATA_DIR.glob('race_*.json'):
        try:
            meeting_dates.append(date.fromisoformat(json.loads(path.read_text(encoding='utf-8'))['meeting_date']))
        except (ValueError, KeyError, OSError):
            continue
    default_date = max(meeting_dates, default=date.today() - timedelta(days=1))
    data_files = sorted(
        (path for path in OUTPUT_DIR.iterdir()
         if path.is_file() and path.suffix in {'.csv', '.json'}
         and not path.name.endswith(('_sectionals.csv', '_dividends.csv', '.manifest.json')) and path.name.startswith('hkjc_')),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    exports = []
    for path in data_files:
        match = re.fullmatch(r'hkjc_([a-z-]+)_(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})', path.stem)
        courses = ', '.join(COURSES.get(code.upper(), code.upper()) for code in match.group(1).split('-')) if match else None
        scope = f"{courses} | {match.group(2)} to {match.group(3)}" if match else path.stem
        exports.append({
            "name": path.name,
            "scope": scope,
            "generated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    latest_export = next((export for export in exports if export['name'].endswith('.csv')), exports[0] if exports else None)
    return render_template('index.html', default_start=default_date, default_end=default_date, exports=exports, latest_export=latest_export)


@app.route('/run', methods=['POST'])
def run_scrape():
    start_str = request.form.get('start_date')
    end_str = request.form.get('end_date')
    if not start_str or not end_str:
        flash('Start and end date are required')
        return redirect(url_for('index'))

    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Dates must use the YYYY-MM-DD format')
        return redirect(url_for('index'))
    if end_date < start_date:
        flash('End date must be on or after start date')
        return redirect(url_for('index'))
    if (end_date - start_date).days > 365:
        flash('Choose a date range of 366 days or less')
        return redirect(url_for('index'))

    if job['running']:
        flash('A job is already running')
        return redirect(url_for('index'))

    selected_courses = sorted(set(request.form.getlist('courses')) & set(COURSES))
    if not selected_courses:
        flash('Select at least one racecourse')
        return redirect(url_for('index'))
    output_format = request.form.get('output_format', 'csv')
    if output_format not in {'csv', 'json', 'both'}:
        flash('Choose CSV, JSON, or both output formats')
        return redirect(url_for('index'))
    output_mode = request.form.get('output_mode', 'single')
    if output_mode not in {'single', 'separate'}:
        flash('Choose combined or per-day files')
        return redirect(url_for('index'))

    t = threading.Thread(target=run_scrape_job, args=(start_date, end_date, selected_courses, output_format, output_mode), daemon=True)
    with job_lock:
        if job['running']:
            flash('A job is already running')
            return redirect(url_for('index'))
        cancel_event.clear()
        job['running'] = True
        t.start()
    flash('Scraping started in background')
    return redirect(url_for('index'))


@app.route('/status')
def status():
    return jsonify({
        'state': job.get('state'), 'expected': job.get('expected'),
        'successful': job.get('successful'), 'races': list(job.get('races', [])),
        'running': job['running'],
        'total': job['total'],
        'done': job['done'],
        'current': job['current'],
        'log': job['log'][-30:],
        'output_file': job['output_file'],
        'downloads': job['downloads'],
        'error': job['error'],
    })


@app.route('/stop', methods=['POST'])
def stop():
    if job['running']:
        cancel_event.set()
        job['log'].append('Stop requested by user')
        flash('Stop requested')
    else:
        flash('No job is running')
    return redirect(url_for('index'))


@app.route('/preview/data')
def preview_data():
    fname = Path(request.args.get('file', '')).name
    file_path = OUTPUT_DIR / fname
    if not fname or not fname.endswith('.csv') or not file_path.is_file() or file_path.parent != OUTPUT_DIR:
        return jsonify({'error': 'CSV file not found'}), 404
    df = pd.read_csv(file_path, nrows=50, usecols=lambda column: column in PREVIEW_COLUMNS)
    df = df[[column for column in PREVIEW_COLUMNS if column in df.columns]]
    records = json.loads(df.astype(object).where(pd.notna(df), None).to_json(orient='records'))
    return jsonify({'filename': fname, 'columns': list(df.columns), 'rows': records, 'limit': 50})


@app.route('/delete_files', methods=['POST'])
def delete_files():
    requested = request.form.getlist('filenames')
    deleted, skipped = [], []
    for raw in requested:
        fname = Path(raw).name
        file_path = OUTPUT_DIR / fname
        if (not fname.startswith('hkjc_') or file_path.is_symlink() or file_path.suffix.lower() not in {'.csv', '.json'}
                or file_path.parent != OUTPUT_DIR or not file_path.is_file()):
            skipped.append(raw)
            continue
        try:
            file_path.unlink()
            deleted.append(fname)
        except OSError:
            skipped.append(fname)
    if not requested:
        flash('Select at least one file to delete')
    if deleted:
        flash(f"Deleted {len(deleted)} file(s): {', '.join(deleted)}")
    if skipped:
        flash(f"Skipped {len(skipped)} file(s): not a CSV/JSON export in output/ or already gone")
    return redirect(url_for('index') + '#saved-exports')


@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == '__main__':
    app.run(port=8501, debug=os.getenv('FLASK_DEBUG') == '1')
