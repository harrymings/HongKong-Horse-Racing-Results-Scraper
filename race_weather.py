"""Source-timestamped HKO observations from the DATA.GOV.HK archive.

No spatial substitution, daily interpolation or synthetic hourly averages.
"""
import csv
import hashlib
import io
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from persistence import atomic_json, save_race

HKT = ZoneInfo('Asia/Hong_Kong')
ARCHIVE = 'https://app.data.gov.hk/v1/historical-archive/'
REGIONAL = 'https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/'
STATIONS = {'Sha Tin': 'SHA', 'Happy Valley': 'HPV'}
SOURCES = {
    'temperature': {'url': REGIONAL+'latest_1min_temperature.csv', 'minutes': 1,
                    'fields': {'temperature_c': 'Air Temperature(degree Celsius)'}},
    'humidity': {'url': REGIONAL+'latest_1min_humidity.csv', 'minutes': 1,
                 'fields': {'humidity_pct': 'Relative Humidity(percent)'}},
    'wind': {'url': REGIONAL+'latest_10min_wind.csv', 'minutes': 10,
             'fields': {'wind_speed_kph': '10-Minute Mean Speed(km/hour)',
                        'wind_direction': '10-Minute Mean Wind Direction(Compass points)',
                        'wind_gust_kph': '10-Minute Maximum Gust(km/hour)'}},
    'rainfall': {'url': 'https://data.weather.gov.hk/weatherAPI/opendata/hourlyRainfall.php?lang=en',
                 'minutes': 60, 'fields': {'rainfall_mm': 'value'}},
}
FIELDS = [key for source in SOURCES.values() for key in source['fields']]


def aware(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    return result.astimezone(HKT)


def declared_starters_url(day, venue_code="ST"):
    date = datetime.fromisoformat(day).strftime('%Y%m%d')
    return f'https://racing.hkjc.com/Racing/Info/MCS/English/racing/prerace/dstr/{date}_{venue_code}0000_S_DSTR.xml.zip'


def parse_schedules(html, day, venue_code):
    """The legacy .xml.zip URL currently serves HTML. Parse its actual payload."""
    if venue_code not in {'ST', 'HV'}:
        raise ValueError('Unsupported venue')
    soup = BeautifulSoup(html, 'lxml')
    venue = {'ST':'SHA TIN', 'HV':'HAPPY VALLEY'}[venue_code]
    expected_date = datetime.fromisoformat(day).strftime('%d/%m/%Y')
    schedules = {}
    for heading in soup.select('h2.meetingInfo'):
        info = heading.get_text(' ', strip=True).upper()
        if f'FOR RACE MEETING ON {expected_date}' not in info or venue not in info:
            continue
        group = heading.find_parent('div', class_='table-responsive')
        if group is None:
            raise ValueError('Declared starters meeting container missing')
        for block in group.select('div.sectionBg[id]'):
            identity = re.fullmatch(re.escape(venue_code)+r'(\d{2})', block['id'])
            header = block.select_one('h3.raceInfo')
            if not identity or not header:
                continue
            raw = header.get_text(' ', strip=True)
            match = re.match(r'RACE\s+(\d+)\s*[-–]\s*(\d{1,2}:\d{2}\s*[AP]M)\b', raw, re.I)
            if not match or int(match[1]) != int(identity[1]):
                raise ValueError('Declared starters race identity/time mismatch')
            number = int(identity[1])
            if number in schedules:
                raise ValueError('Duplicate scheduled race')
            clock = datetime.strptime(match[2].upper(), '%I:%M %p').time()
            start = datetime.combine(datetime.fromisoformat(day).date(), clock, HKT)
            schedules[number] = {'scheduled_start_at_hkt':start.isoformat(),
                'actual_start_at_hkt':None, 'time_precision':'minute',
                'start_time_kind':'SCHEDULED', 'start_time_text':raw.split('Rating Range')[0].strip(),
                'timezone':'Asia/Hong_Kong'}
    if not schedules:
        raise ValueError('Requested meeting absent from declared starters')
    return schedules


def parse_observations(payload, dataset, station, archived_at, source_url):
    spec = SOURCES[dataset]
    archive_time = aware(archived_at)
    if dataset == 'rainfall':
        data = json.loads(payload)
        if not isinstance(data.get('hourlyRainfall'), list):
            raise ValueError('Unexpected hourly rainfall schema')
        records = [(row, aware(data['obsTime'])) for row in data['hourlyRainfall']
                   if row.get('automaticWeatherStation') == station]
    else:
        reader = csv.DictReader(io.StringIO(payload.lstrip('\ufeff')))
        required = {'Date time', 'Automatic Weather Station', *spec['fields'].values()}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Unexpected HKO '+dataset+' CSV schema')
        records = [(row, datetime.strptime(row['Date time'], '%Y%m%d%H%M').replace(tzinfo=HKT))
                   for row in reader if row['Automatic Weather Station'] == station]
    if len(records) > 1:
        raise ValueError('Duplicate station observations')
    output = {}
    for row, observed_at in records:
        if dataset == 'rainfall' and row.get('unit') != 'mm':
            raise ValueError('Unexpected rainfall units')
        if observed_at > archive_time:
            raise ValueError('Observation occurs after its archive capture')
        for field, column in spec['fields'].items():
            raw = str(row.get(column, '')).strip()
            if field == 'wind_direction':
                value = raw if raw and raw != 'N/A' else None
            else:
                try:
                    value = 0.0 if raw == 'Calm' and field == 'wind_speed_kph' else float(raw)
                    if not math.isfinite(value):
                        value = None
                except ValueError:
                    value = None
            if value is not None and field == 'humidity_pct' and not 0 <= value <= 100:
                raise ValueError('Invalid humidity')
            if value is not None and field in {'wind_speed_kph','wind_gust_kph','rainfall_mm'} and value < 0:
                raise ValueError('Negative wind/rainfall value')
            output[field] = {'value':value, 'raw_text':raw,
                'status':'AVAILABLE' if value is not None else 'MISSING_AT_SOURCE',
                'unit':{'temperature_c':'degree Celsius','humidity_pct':'percent','wind_speed_kph':'km/hour','wind_gust_kph':'km/hour','wind_direction':'compass points','rainfall_mm':'mm'}[field],
                'station':station, 'station_code':STATIONS[station],
                'source_station_id':row.get('automaticWeatherStationID'),
                'source_dataset':'HKO_'+dataset.upper(), 'source_url':source_url,
                'original_source_url':spec['url'], 'observed_at':observed_at.isoformat(),
                'observation_period_start':(observed_at-timedelta(minutes=spec['minutes'])).isoformat(),
                'observation_period_end':observed_at.isoformat(),
                'interval_minutes':spec['minutes'],
                'resolution':'hourly' if spec['minutes'] == 60 else 'subhourly',
                'archive_captured_at':archive_time.isoformat(),
                'known_at':(archive_time+timedelta(minutes=1)).isoformat(), 'availability_precision':'archive minute; conservative end-of-minute upper bound', 'availability_basis':'public_archive_capture_upper_bound',
                'provisional':True}
    return output


class RaceWeatherProvider:
    def __init__(self, cache_dir=None, client=None, rate_limit=1.0):
        self.cache_dir = Path(cache_dir or Path(__file__).parent/'weather_cache')
        self.client = client or httpx.Client(timeout=25, follow_redirects=True)
        self.rate_limit = rate_limit
        self.last_request = 0.0
        self.memory = {}

    def close(self):
        self.client.close()

    def _fetch(self, url, max_age=86400, refresh=False):
        key = hashlib.sha256(url.encode()).hexdigest()
        now = time.time()
        record = self.memory.get(key)
        path = self.cache_dir/(key+'.json')
        if record is None and path.exists():
            record = json.loads(path.read_text(encoding='utf-8'))
        if not refresh and record and now-record['fetched_at_epoch'] < (min(max_age,86400) if record['status_code'] == 404 else max_age):
            return record['status_code'], record['text']
        time.sleep(max(0, self.rate_limit-(time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        response = self.client.get(url)
        if response.status_code != 404:
            response.raise_for_status()
        # Do not cache malformed/transport failures; JSON parsing is validated by callers.
        record = {'status_code':response.status_code,'text':response.text,'fetched_at_epoch':now,'url':url}
        self.memory[key] = record
        atomic_json(path,record)
        return response.status_code, response.text

    def schedules(self, day, venue_code):
        url = declared_starters_url(day, venue_code)
        code, body = self._fetch(url)
        if code == 404:
            raise ValueError('Declared starters unavailable')
        try:
            schedules = parse_schedules(body, day, venue_code)
        except ValueError:
            self.invalidate(url)
            raise
        for schedule in schedules.values():
            schedule['start_time_source'] = url
            schedule['start_time_fetched_at'] = datetime.now(timezone.utc).isoformat()
            # A currently accessible historical card does not prove its original publication cutoff.
            schedule['start_time_known_at'] = None
        return schedules

    def invalidate(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        self.memory.pop(key, None)
        (self.cache_dir/(key+'.json')).unlink(missing_ok=True)

    def match(self, race, max_age_minutes=30):
        if not 0 < max_age_minutes <= 120:
            raise ValueError('max_age_minutes must be in (0,120]')
        report = {'status':'MISSING_START_TIME','resolution':'mixed', 'fields':{},
            'station':race.get('venue'), 'station_code':STATIONS.get(race.get('venue')),
            'match_policy':'latest archive capture and observation at or before target; exact venue station only',
            'max_age_minutes':max_age_minutes,'fetched_at':datetime.now(timezone.utc).isoformat(),
            'is_pre_race_available':False,'pre_race_verified':False,
            'attribution':'Hong Kong Observatory, HKSAR Government and DATA.GOV.HK'}
        for field in FIELDS:
            report[field] = None
        if race.get('race_status') == 'ABANDONED':
            report['status'] = 'NOT_APPLICABLE_ABANDONED'
            return report
        actual = race.get('actual_start_at_hkt') if race.get('actual_start_source') else None
        target_raw = actual or race.get('scheduled_start_at_hkt')
        if race.get('actual_start_at_hkt') and not actual:
            report['actual_start_warning'] = 'Ignored actual timestamp without an independent source'
        if not target_raw or race.get('venue') not in STATIONS:
            return report
        target = aware(target_raw)
        if target.date().isoformat() != race['meeting_date']:
            raise ValueError('Race start date does not match meeting date')
        report.update(target_at=target.isoformat(), target_kind='ACTUAL' if actual else 'SCHEDULED')
        if target.date() >= datetime.now(HKT).date():
            report['status'] = 'ARCHIVE_NOT_YET_AVAILABLE'
            return report
        days = {target.date(), (target-timedelta(minutes=max_age_minutes)).date()}
        for dataset, spec in SOURCES.items():
            fields = {f:{'value':None,'status':'MISSING_AT_SOURCE','original_source_url':spec['url']} for f in spec['fields']}
            try:
                candidates = []
                for day in sorted(days):
                    list_url = ARCHIVE+'list-file-versions?'+urlencode({'url':spec['url'],'start':day.strftime('%Y%m%d'),'end':day.strftime('%Y%m%d')})
                    code, body = self._fetch(list_url)
                    if code == 404:
                        for info in fields.values():
                            info.update(status='ARCHIVE_UNAVAILABLE',source_url=list_url)
                        continue
                    try:
                        data = json.loads(body)
                        stamps = data['timestamps']
                        if not isinstance(stamps,list):
                            raise ValueError('Invalid archive version schema')
                        if len(stamps) >= 10000:
                            raise ValueError('Archive results may be truncated')
                        for stamp in stamps:
                            parsed = datetime.strptime(stamp,'%Y%m%d-%H%M').replace(tzinfo=HKT)
                            if parsed+timedelta(minutes=1) <= target and (target-parsed).total_seconds() <= max_age_minutes*60:
                                candidates.append((parsed,stamp))
                    except (ValueError,KeyError,TypeError):
                        self.invalidate(list_url)
                        raise ValueError('Invalid archive version response')
                if candidates:
                    archive_time, stamp = max(candidates)
                    url = ARCHIVE+'get-file?'+urlencode({'url':spec['url'],'time':stamp})
                    code, body = self._fetch(url, max_age=365*86400)
                    if code == 404:
                        for info in fields.values():
                            info.update(status='MISSING_AT_SOURCE',source_url=url)
                    else:
                        try:
                            parsed = parse_observations(body,dataset,race['venue'],archive_time.isoformat(),url)
                        except (ValueError,KeyError,TypeError):
                            self.invalidate(url)
                            raise ValueError('Invalid '+dataset+' observation response')
                        for field in fields:
                            info = parsed.get(field)
                            if info:
                                age = (target-aware(info['observed_at'])).total_seconds()/60
                                info['age_minutes'] = age
                                if not 0 <= age <= max_age_minutes:
                                    info.update(value=None,status='STALE_OR_FUTURE_OBSERVATION')
                                fields[field] = info
                            else:
                                fields[field].update(status='STATION_NOT_REPORTED',source_url=url)
                else:
                    for info in fields.values():
                        if info['status'] == 'MISSING_AT_SOURCE':
                            info['status'] = 'NO_RECENT_ARCHIVE'
            except httpx.HTTPError as exc:
                for info in fields.values():
                    info.update(value=None,status='FETCH_FAILED',error=type(exc).__name__)
            except (ValueError,KeyError,TypeError) as exc:
                for info in fields.values():
                    info.update(value=None,status='PARSE_FAILED',error=str(exc))
            report['fields'].update(fields)
        available = 0
        for field, info in report['fields'].items():
            report[field] = info.get('value')
            available += info.get('status') == 'AVAILABLE'
        report['status'] = 'AVAILABLE' if available == len(FIELDS) else 'PARTIAL' if available else 'UNAVAILABLE'
        # Weather archives alone cannot establish that an historical race schedule/entry existed by cutoff.
        report['weather_available_by_target'] = bool(available)
        return report


def enrich_race_time_weather(race, provider, path=None, max_age_minutes=30):
    """Only non-null verified fields replace earlier matches for the exact same target."""
    if race.get('race_status') != 'ABANDONED':
        try:
            schedule = provider.schedules(race['meeting_date'],race['venue_code']).get(race['race_number'])
            if not schedule:
                raise ValueError('Race absent from declared starters')
            schedule.pop('actual_start_at_hkt',None)
            race.update(schedule)
            race['start_time_status'] = 'AVAILABLE'
        except (ValueError,httpx.HTTPError) as exc:
            race['start_time_status'] = 'REFRESH_FAILED_RETAINED' if race.get('scheduled_start_at_hkt') else 'UNAVAILABLE'
            race['start_time_error'] = str(exc)
    fresh = provider.match(race,max_age_minutes=max_age_minutes)
    previous = race.get('race_time_weather') or {}
    if previous.get('target_at') == fresh.get('target_at') and previous.get('station') == fresh.get('station'):
        for field in FIELDS:
            old = previous.get('fields',{}).get(field,{})
            try:
                old_end = aware(old['observation_period_end'])
                cutoff = aware(fresh['target_at'])
                eligible = (old.get('station') == fresh.get('station') and
                            0 <= (cutoff-old_end).total_seconds() <= max_age_minutes*60 and
                            aware(old['known_at']) <= cutoff)
            except (ValueError,KeyError,TypeError):
                eligible = False
            if eligible and old.get('status') == 'AVAILABLE' and fresh.get(field) is None:
                fresh.setdefault('refresh_errors',{})[field] = fresh.get('fields',{}).get(field,{})
                fresh['fields'][field] = old
                fresh[field] = old['value']
        count = sum(info.get('status') == 'AVAILABLE' for info in fresh.get('fields',{}).values())
        fresh['weather_available_by_target'] = bool(count)
        if fresh['status'] not in {'NOT_APPLICABLE_ABANDONED','MISSING_START_TIME'}:
            fresh['status'] = 'AVAILABLE' if count == len(FIELDS) else 'PARTIAL' if count else fresh['status']
    race['race_time_weather'] = fresh
    return save_race(path,race) if path else race
