import copy
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from race_weather import (RaceWeatherProvider, SOURCES, enrich_race_time_weather,
                          parse_observations, parse_schedules)
from persistence import save_race

FIXTURES=Path(__file__).parent/'fixtures'


def source_transport(request):
    query=parse_qs(request.url.query.decode())
    if 'DSTR' in str(request.url):
        return httpx.Response(200,text=(FIXTURES/'declared_starters_2026-11-14.html').read_text())
    original=query.get('url',[''])[0]
    dataset=next((key for key,spec in SOURCES.items() if spec['url']==original),None)
    if dataset=='rainfall':
        return httpx.Response(404,json={'message':'Not Found'})
    if not dataset:
        raise AssertionError('Unexpected network request '+str(request.url))
    name=f'hko_archive_{dataset}_versions.json' if 'list-file-versions' in request.url.path else f'hko_archive_{dataset}.csv'
    return httpx.Response(200,text=(FIXTURES/name).read_text())


@pytest.fixture
def provider(tmp_path):
    p=RaceWeatherProvider(tmp_path/'cache',httpx.Client(transport=httpx.MockTransport(source_transport)),rate_limit=0)
    yield p
    p.close()


def race():
    return {'race_id':'one','meeting_date':'2026-11-14','venue':'Sha Tin','venue_code':'ST',
            'race_number':1,'race_status':'PUBLISHED'}


def test_official_schedule_and_weather(provider):
    result=enrich_race_time_weather(race(),provider)
    assert result['scheduled_start_at_hkt']=='2026-11-14T12:30:00+08:00'
    assert result.get('actual_start_at_hkt') is None
    weather=result['race_time_weather']
    assert weather['temperature_c']==23.8
    assert weather['humidity_pct']==92
    assert weather['wind_speed_kph']==5
    assert weather['wind_gust_kph']==10
    assert weather['wind_direction']=='Northeast'
    assert weather['rainfall_mm'] is None
    assert weather['fields']['rainfall_mm']['status']=='ARCHIVE_UNAVAILABLE'
    assert weather['fields']['temperature_c']['observed_at']=='2026-11-14T12:20:00+08:00'
    assert weather['fields']['temperature_c']['known_at']=='2026-11-14T12:29:00+08:00'
    assert weather['fields']['temperature_c']['interval_minutes']==1
    assert weather['fields']['wind_speed_kph']['interval_minutes']==10
    assert weather['status']=='PARTIAL'
    assert not weather['pre_race_verified']


def test_schedule_exact_meeting_and_venue():
    html=(FIXTURES/'declared_starters_2026-11-14.html').read_text()
    schedules=parse_schedules(html,'2026-11-14','ST')
    assert len(schedules)==11
    assert schedules[11]['scheduled_start_at_hkt'].endswith('18:10:00+08:00')
    with pytest.raises(ValueError):
        parse_schedules(html,'2026-11-11','ST')
    with pytest.raises(ValueError):
        parse_schedules(html,'2026-11-14','HV')
    with pytest.raises(ValueError):
        parse_schedules(html.replace('RACE 1 -','RACE 2 -',1),'2026-11-14','ST')


def test_no_station_substitution(provider):
    target=race();target.update(venue='Happy Valley',venue_code='HV',scheduled_start_at_hkt='2026-11-14T12:30:00+08:00')
    weather=provider.match(target)
    assert weather['temperature_c']==26
    assert weather['humidity_pct'] is None and weather['wind_speed_kph'] is None
    assert weather['fields']['humidity_pct']['status']=='STATION_NOT_REPORTED'
    assert weather['fields']['temperature_c']['station_code']=='HPV'


def test_missing_stale_and_future(provider):
    assert provider.match(race())['status']=='MISSING_START_TIME'
    target=race();target['scheduled_start_at_hkt']='2026-11-14T12:15:00+08:00'
    result=provider.match(target)
    # Mock returns the 12:20 source observation for an earlier archive; reject it.
    assert result['temperature_c'] is None
    assert result['fields']['temperature_c']['status']=='PARSE_FAILED'
    target['scheduled_start_at_hkt']='2026-11-14T12:30:00+08:00'
    assert provider.match(target,max_age_minutes=5)['temperature_c'] is None
    target['scheduled_start_at_hkt']='2026-11-13T12:30:00+08:00'
    with pytest.raises(ValueError):
        provider.match(target)
    target['scheduled_start_at_hkt']='2026-11-14T12:30:00'
    with pytest.raises(ValueError):
        provider.match(target)


def test_rainfall_interval_from_real_schema():
    # Synthetic values, based on the official current hourlyRainfall JSON schema.
    payload=json.dumps({'obsTime':'2026-11-14T12:15:00+08:00','hourlyRainfall':[
        {'automaticWeatherStation':'Sha Tin','automaticWeatherStationID':'RF020','value':'0.5','unit':'mm'}]})
    parsed=parse_observations(payload,'rainfall','Sha Tin','2026-11-14T12:28:00+08:00','fixture://rain')
    assert parsed['rainfall_mm']['value']==.5
    assert parsed['rainfall_mm']['resolution']=='hourly'
    assert parsed['rainfall_mm']['observation_period_start']=='2026-11-14T11:15:00+08:00'


def test_failed_refresh_and_changed_target(provider,tmp_path):
    path=tmp_path/'race.json'
    original=enrich_race_time_weather(race(),provider,path)
    def fail(request):
        raise httpx.ConnectError('offline')
    broken=RaceWeatherProvider(tmp_path/'other',httpx.Client(transport=httpx.MockTransport(fail)),rate_limit=0)
    try:
        retained=enrich_race_time_weather(copy.deepcopy(original),broken,path)
        assert retained['race_time_weather']['temperature_c']==23.8
        assert retained['race_time_weather']['refresh_errors']['temperature_c']['status']=='FETCH_FAILED'
        changed=copy.deepcopy(original)
        changed['scheduled_start_at_hkt']='2026-11-14T13:30:00+08:00'
        result=enrich_race_time_weather(changed,broken,path)
        assert result['race_time_weather']['temperature_c'] is None
        assert json.loads(path.read_text())['race_time_weather']['temperature_c'] is None
    finally:
        broken.close()


def test_cache_and_independent_failure(tmp_path):
    calls=[]
    def transport(request):
        calls.append(str(request.url))
        if 'humidity' in str(request.url):
            raise httpx.ConnectError('humidity unavailable')
        return source_transport(request)
    p=RaceWeatherProvider(tmp_path/'cache',httpx.Client(transport=httpx.MockTransport(transport)),rate_limit=0)
    target=race();target['scheduled_start_at_hkt']='2026-11-14T12:30:00+08:00'
    result=p.match(target)
    assert result['temperature_c']==23.8 and result['humidity_pct'] is None
    first=list(calls)
    p.match(target)
    added=calls[len(first):]
    assert added and all('humidity' in url for url in added)
    p.close()


def test_abandoned_has_no_race_time_match(provider):
    target=race();target['race_status']='ABANDONED'
    result=enrich_race_time_weather(target,provider)
    assert result['race_time_weather']['status']=='NOT_APPLICABLE_ABANDONED'
    assert result.get('scheduled_start_at_hkt') is None


def test_actual_start_is_not_replaced_by_schedule(provider):
    target=race();target['actual_start_at_hkt']='2026-11-14T12:30:00+08:00';target['actual_start_source']='fixture://independent-off-time'
    result=enrich_race_time_weather(target,provider)
    assert result['actual_start_at_hkt']==target['actual_start_at_hkt']
    assert result['race_time_weather']['target_kind']=='ACTUAL'


def test_archive_capture_minute_cannot_cross_cutoff(provider):
    target=race();target['scheduled_start_at_hkt']='2026-11-14T12:28:00+08:00'
    result=provider.match(target)
    # 12:28 snapshot could be captured after 12:28:00; prefer a strictly earlier minute.
    field=result['fields']['temperature_c']
    if field['status']=='AVAILABLE':
        assert field['known_at']<=target['scheduled_start_at_hkt']
        assert field['archive_captured_at']<'2026-11-14T12:28:00+08:00'


def test_invalid_schema_is_not_cached(tmp_path):
    calls=[]
    def transport(request):
        calls.append(str(request.url))
        if 'temperature' in str(request.url):
            return httpx.Response(200,text='changed schema')
        return source_transport(request)
    provider=RaceWeatherProvider(tmp_path/'cache',httpx.Client(transport=httpx.MockTransport(transport)),rate_limit=0)
    target=race();target['scheduled_start_at_hkt']='2026-11-14T12:30:00+08:00'
    assert provider.match(target)['fields']['temperature_c']['status']=='PARSE_FAILED'
    count=len(calls)
    provider.match(target)
    assert any('temperature' in url for url in calls[count:])
    provider.close()


def test_stricter_age_limit_does_not_reuse_old_match(provider,tmp_path):
    path=tmp_path/'race.json'
    original=enrich_race_time_weather(race(),provider,path)
    assert original['race_time_weather']['temperature_c']==23.8
    strict=enrich_race_time_weather(original,provider,path,max_age_minutes=5)
    assert strict['race_time_weather']['temperature_c'] is None
    assert json.loads(path.read_text())['race_time_weather']['temperature_c'] is None
