from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
import json
import os
import re
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd
import time
import threading
import httpx
from urllib.parse import parse_qs, urlparse
from scraper_incremental import DATA_DIR, DEFAULT_BASE_URL, IncrementalScraper, RaceNotPublished

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


# In-memory job status
job = {
    'running': False,
    'total': 0,
    'done': 0,
    'current': '',
    'log': [],
    'output_file': None,
    'downloads': [],
    'error': None,
}


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
                "HorseId": runner["horse_id"],
                "JockeyId": runner["jockey_id"],
                "TrainerId": runner["trainer_id"],
            })
    return rows


def _save_exports(races, file_stem, output_format):
    downloads = []
    if output_format in {"csv", "both"}:
        csv_name = f"{file_stem}.csv"
        pd.DataFrame(_race_rows(races)).to_csv(OUTPUT_DIR / csv_name, index=False, encoding='utf-8-sig')
        downloads.append(csv_name)
    if output_format in {"json", "both"}:
        json_name = f"{file_stem}.json"
        (OUTPUT_DIR / json_name).write_text(json.dumps(races, indent=2), encoding="utf-8")
        downloads.append(json_name)
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
            for month in response.json()["stn"]["data"]:
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


def _fetch_venue_weather(venue, meeting_date, ryes_cache, clm_cache):
    """Daily weather from the HKO station AT the racecourse (SHA = Sha Tin, HPV = Happy Valley).

    Max/min temp come from the RYES daily bulletin (published ~01:30 the next day,
    so available to yesterday). Mean temp comes from the CLMTEMP climate dataset,
    which loads with a longer lag (~1 month). Both are whole-day values for the
    meeting date at the racecourse station -- not race-time readings.
    """
    station_code = VENUE_STATIONS.get((venue or '').strip())
    if not station_code:
        return None
    camel = venue.strip().replace(' ', '')  # RYES JSON keys drop spaces: 'Sha Tin' -> 'ShaTinMaxTemp'
    ryes_key = (station_code, meeting_date.isoformat())
    if ryes_key not in ryes_cache:
        report = None
        try:
            response = httpx.get(HKO_OPEN_DATA, params={
                'dataType': 'RYES', 'lang': 'en',
                'date': meeting_date.strftime('%Y%m%d'), 'station': station_code,
            }, timeout=20, follow_redirects=True)
            if response.status_code == 200:
                data = response.json()
                max_t = _weather_number(data.get(f'{camel}MaxTemp'))
                min_t = _weather_number(data.get(f'{camel}MinTemp'))
                if max_t is not None or min_t is not None:
                    report = {
                        'date': meeting_date.isoformat(),
                        'station': venue.strip(),
                        'station_code': station_code,
                        'max_temp_c': max_t,
                        'min_temp_c': min_t,
                        'mean_temp_c': None,
                        'source_url': f'{HKO_OPEN_DATA}?dataType=RYES&date={meeting_date.strftime("%Y%m%d")}&station={station_code}',
                    }
        except (httpx.HTTPError, ValueError):
            report = None
        ryes_cache[ryes_key] = report
    report = ryes_cache[ryes_key]
    if report is None:
        return None
    if report.get('mean_temp_c') is None:
        clm_key = (station_code, meeting_date.year, meeting_date.month)
        if clm_key not in clm_cache:
            monthly = {}
            try:
                response = httpx.get(HKO_OPEN_DATA, params={
                    'dataType': 'CLMTEMP', 'lang': 'en', 'rformat': 'json',
                    'station': station_code, 'year': meeting_date.year, 'month': meeting_date.month,
                }, timeout=30, follow_redirects=True)
                if response.status_code == 200:
                    for row in response.json().get('data', []):
                        if len(row) >= 4 and str(row[1]) == str(meeting_date.month):
                            try:
                                monthly[int(row[2])] = float(row[3])
                            except (TypeError, ValueError):
                                continue
            except (httpx.HTTPError, ValueError):
                pass
            clm_cache[clm_key] = monthly
        report['mean_temp_c'] = clm_cache[clm_key].get(meeting_date.day)
    return report


def scrape_date(scraper, meet_date_str, selected_courses):
    races = []
    iso_date = datetime.strptime(meet_date_str, "%d/%m/%Y").strftime("%Y/%m/%d")
    scraper.base_url = BASE_URL_TEMPLATE.format(date=iso_date)
    for race_url in scraper.list_recent_race_urls():
        if _course_from_url(race_url) not in selected_courses:
            continue
        try:
            race = scraper.parse_and_save(race_url)
        except RaceNotPublished:
            job['log'].append(f"No published results yet: {race_url}")
            continue
        except Exception as exc:
            job['log'].append(f"Failed {race_url}: {exc}")
            continue
        races.append(race)
        time.sleep(scraper.rate_limit)

    return races


def run_scrape_job(start_date, end_date, selected_courses, output_format='csv', output_mode='single'):
    job['running'] = True
    job['log'] = [f"Job started: {start_date} to {end_date}"]
    dates = [d for d in daterange(start_date, end_date)]
    job['total'] = len(dates)
    job['done'] = 0
    job['output_file'] = None
    job['downloads'] = []
    job['error'] = None
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
            if not job['running']:
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
                for race in races:
                    race['weather'] = weather
                    try:
                        race['venue_weather'] = _fetch_venue_weather(race.get('venue'), d, ryes_cache, clm_months)
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        race['venue_weather'] = None
                        job['log'].append(f"Racecourse station weather unavailable for {race.get('venue')} {meet_date}: {exc}")
                (matched_dates if weather else missing_dates).add(d.isoformat())
                races_by_date.append((d, races))
                runner_count = sum(len(race['runners']) for race in races)
                job['log'].append(f"Collected {len(races)} races and {runner_count} runners")
                if output_mode == 'separate':
                    course_slug = "-".join(sorted(selected_courses)).lower()
                    stem = f"hkjc_{course_slug}_{d.strftime('%Y-%m-%d')}"
                    job['downloads'].extend(_save_exports(races, stem, output_format))
            else:
                job['log'].append(f"No published race results for {meet_date}")
            job['done'] += 1
            time.sleep(0.1)

        if races_by_date and output_mode == 'single':
            all_races = [race for _, races in races_by_date for race in races]
            course_slug = "-".join(sorted(selected_courses)).lower()
            stem = f"hkjc_{course_slug}_{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}"
            job['downloads'].extend(_save_exports(all_races, stem, output_format))
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
        job['running'] = False
        job['current'] = ''
        job['log'].append('Job finished')


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
         and not path.name.endswith(('_sectionals.csv', '_dividends.csv'))),
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
    t.start()
    flash('Scraping started in background')
    return redirect(url_for('index'))


@app.route('/status')
def status():
    return jsonify({
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
        job['running'] = False
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
        if (not fname or file_path.suffix.lower() not in {'.csv', '.json'}
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
