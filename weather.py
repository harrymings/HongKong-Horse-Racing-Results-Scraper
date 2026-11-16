"""Daily weather enrichment shared by scraping and historical backfills."""
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlencode
import httpx
from persistence import merge_non_null, merge_weather, save_race

HKO_OPEN_DATA = 'https://data.weather.gov.hk/weatherAPI/opendata/opendata.php'
VENUE_STATIONS = {'Sha Tin': 'SHA', 'Happy Valley': 'HPV'}


def _number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def fetch_venue_weather(venue, meeting_date, ryes_cache, clm_cache):
    station = VENUE_STATIONS.get(venue)
    if not station:
        return None
    day = meeting_date.isoformat()
    report = {'date': day, 'station': venue, 'station_code': station,
              'resolution': 'daily', 'known_at': None, 'is_pre_race_available': False,
              'observation_period_start': day + 'T00:00:00+08:00',
              'observation_period_end': (meeting_date + timedelta(days=1)).isoformat() + 'T00:00:00+08:00',
              'field_sources': {}, 'fetched_at': datetime.now(timezone.utc).isoformat()}
    # Each field is independent. Cache source misses, but never cache transport/parse failures.
    for dataset, field in [('CLMTEMP', 'mean_temp_c'), ('CLMMAXT', 'max_temp_c'), ('CLMMINT', 'min_temp_c')]:
        params = dict(dataType=dataset, lang='en', rformat='json', station=station,
                      year=meeting_date.year, month=meeting_date.month)
        url = HKO_OPEN_DATA + '?' + urlencode(params)
        key = (dataset, station, meeting_date.year, meeting_date.month)
        status, error, value = 'MISSING_AT_SOURCE', None, None
        try:
            if key not in clm_cache:
                response = httpx.get(url, timeout=30, follow_redirects=True)
                if response.status_code == 404:
                    clm_cache[key] = []
                else:
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                        raise ValueError('Unexpected HKO climate schema')
                    clm_cache[key] = payload['data']
            for row in clm_cache[key]:
                if len(row) >= 4 and [str(int(x)) for x in row[:3]] == [str(meeting_date.year), str(meeting_date.month), str(meeting_date.day)]:
                    value = _number(row[3])
                    status = 'AVAILABLE' if value is not None else 'MISSING_AT_SOURCE'
                    if len(row) > 4 and row[4] != 'C':
                        status = 'INCOMPLETE_AT_SOURCE'
                    break
        except httpx.HTTPError as exc:
            status, error = 'FETCH_FAILED', str(exc)
        except (ValueError, TypeError) as exc:
            status, error = 'PARSE_FAILED', str(exc)
        report[field] = value
        report['field_sources'][field] = dict(source_dataset=dataset, source_url=url, station=station,
                                              observation_date=day, resolution='daily', status=status, error=error)
    # RYES is only available from 2019-09-10, and supplements missing climate max/min.
    if meeting_date >= date(2019, 9, 10) and any(report[f] is None for f in ('max_temp_c', 'min_temp_c')):
        params = dict(dataType='RYES', lang='en', date=meeting_date.strftime('%Y%m%d'), station=station)
        url = HKO_OPEN_DATA + '?' + urlencode(params)
        try:
            if (station, day) not in ryes_cache:
                response = httpx.get(url, timeout=20, follow_redirects=True)
                if response.status_code == 404:
                    ryes_cache[(station, day)] = {}
                else:
                    response.raise_for_status()
                    ryes_cache[(station, day)] = response.json()
            data = ryes_cache[(station, day)]
            for field, suffix in [('max_temp_c', 'MaxTemp'), ('min_temp_c', 'MinTemp')]:
                value = _number(data.get(venue.replace(' ', '') + suffix))
                if report[field] is None and value is not None:
                    report[field] = value
                    report['field_sources'][field].update(source_dataset='RYES', source_url=url, status='AVAILABLE')
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            report['ryes_error'] = str(exc)
    report['source_url'] = report['field_sources']['mean_temp_c']['source_url']
    return report


def enrich_race_weather(race, daily_provider, venue_provider, path=None):
    day = date.fromisoformat(race['meeting_date'])
    errors = {}
    for key, provider in [('weather', lambda: daily_provider(day)),
                          ('venue_weather', lambda: venue_provider(race.get('venue'), day))]:
        try:
            fresh = provider()
            if fresh:
                fresh.update(resolution='daily', known_at=None, is_pre_race_available=False)
                fresh.setdefault('fetched_at',datetime.now(timezone.utc).isoformat())
                fresh.setdefault('observation_period_start',day.isoformat()+'T00:00:00+08:00')
                fresh.setdefault('observation_period_end',(day+timedelta(days=1)).isoformat()+'T00:00:00+08:00')
                if key == 'weather':
                    fresh.setdefault('field_sources',{field:{'source_dataset':'HKO_DAILY_EXTRACT',
                        'source_url':fresh.get('source_url'),'station':fresh.get('station'),
                        'observation_date':day.isoformat(),'resolution':'daily',
                        'status':'AVAILABLE' if value is not None else 'MISSING_AT_SOURCE'}
                        for field,value in fresh.items() if field.endswith(('_c','_pct','_mm','_hpa'))})
                race[key] = merge_non_null(race.get(key), merge_weather(race.get(key) or {}, fresh))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            errors[key] = str(exc)
    race['weather_errors'] = errors
    return save_race(path, race) if path else race
