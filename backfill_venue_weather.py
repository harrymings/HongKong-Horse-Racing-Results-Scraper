"""Backfill racecourse-station weather into scraped race JSONs and regenerate exports.

Attaches `venue_weather` (daily max/min/mean temperature from the HKO station at
each racecourse: SHA = Sha Tin, HPV = Happy Valley) to every race JSON under
data/, then regenerates the existing export files in output/ from their
filename-encoded scope -- without re-scraping HKJC.

Run again any time (e.g. after HKO publishes more dates); already-matched dates
are re-fetched cheaply and idempotently.
"""
import json
import re
import time
from datetime import date
from pathlib import Path

from scraper_incremental import DATA_DIR
from dashboard import (_fetch_daily_weather, _fetch_venue_weather, _race_rows,
                       _save_exports, OUTPUT_DIR)

VENUE_TO_CODE = {"Sha Tin": "ST", "Happy Valley": "HV"}


def main():
    ryes_cache, clm_cache, weather_months = {}, {}, {}
    races = []
    for path in sorted(DATA_DIR.glob('race_*.json')):
        try:
            race = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            print(f"skip {path.name}: {exc}")
            continue
        meeting_date = date.fromisoformat(race['meeting_date'])
        try:
            race['weather'] = _fetch_daily_weather(meeting_date, weather_months)
        except Exception as exc:
            race['weather'] = None
            print(f"  HKO daily extract unavailable for {race['meeting_date']}: {exc}")
        try:
            race['venue_weather'] = _fetch_venue_weather(race.get('venue'), meeting_date, ryes_cache, clm_cache)
        except Exception as exc:
            race['venue_weather'] = None
            print(f"  venue weather unavailable for {race.get('venue')} {race['meeting_date']}: {exc}")
        path.write_text(json.dumps(race, indent=2), encoding='utf-8')
        races.append(race)
        time.sleep(0.15)

    matched = sum(1 for race in races if race.get('venue_weather'))
    print(f"Backfilled {len(races)} race file(s); racecourse-station weather matched {matched}")

    # Regenerate every existing export from its filename-encoded scope.
    for path in sorted(list(OUTPUT_DIR.glob('hkjc_*.csv')) + list(OUTPUT_DIR.glob('hkjc_*.json'))):
        match = re.fullmatch(r'hkjc_([a-z-]+)_(\d{4}-\d{2}-\d{2})(?:_to_(\d{4}-\d{2}-\d{2}))?', path.stem)
        if not match:
            continue
        courses = {code.upper() for code in match.group(1).split('-')}
        start = date.fromisoformat(match.group(2))
        end = date.fromisoformat(match.group(3) or match.group(2))
        scoped = [
            race for race in races
            if start <= date.fromisoformat(race['meeting_date']) <= end
            and VENUE_TO_CODE.get((race.get('venue') or '').strip()) in courses
        ]
        if not scoped:
            print(f"  no races for {path.name}; left as-is")
            continue
        _save_exports(scoped, path.stem, 'csv' if path.suffix == '.csv' else 'json')
        print(f"  regenerated {path.name} ({len(scoped)} races)")


if __name__ == '__main__':
    main()
