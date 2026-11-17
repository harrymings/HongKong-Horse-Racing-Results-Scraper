import json
import os
from pathlib import Path
from datetime import date
import pytest
import httpx
from test_integrity import parsed, FIXTURES, URL


def test_export_contract(parsed,tmp_path,monkeypatch):
    import dashboard
    monkeypatch.setattr(dashboard,'OUTPUT_DIR',tmp_path)
    dashboard.job.update(expected=1,successful=1,races=[{'status':'SUCCESS','url':URL}],error=None)
    dashboard.cancel_event.clear()
    files=dashboard._save_exports([parsed],'hkjc_test','both',dashboard.job)
    import pandas as pd
    csv=tmp_path/files[0]
    assert csv.read_bytes().startswith(b'\xef\xbb\xbf')
    df=pd.read_csv(csv)
    assert len(df)==14
    assert df.iloc[0]['HorseCode']=='L438'
    assert df.iloc[0]['Timezone']=='Asia/Hong_Kong'
    assert json.loads((tmp_path/files[1]).read_text())[0]['runners'][0]['horse_code']=='L438'
    manifest=json.loads((tmp_path/'hkjc_test.manifest.json').read_text())
    assert manifest['collection_status']=='SUCCESS'
    assert manifest['runner_count']==14
    assert manifest['validation_status']=='PASSED'


def test_weather_independent_sources(monkeypatch):
    import weather
    def get(url,**kwargs):
        if 'CLMMAXT' in url:
            raise httpx.ConnectError('unavailable')
        payload=json.loads((FIXTURES/'hko_clmtemp_sample.json').read_text())
        return httpx.Response(200,json=payload,request=httpx.Request('GET',url))
    monkeypatch.setattr(weather.httpx,'get',get)
    result=weather.fetch_venue_weather('Sha Tin',date(2018,11,14),{}, {})
    assert result['mean_temp_c']==23.4
    assert result['max_temp_c'] is None
    assert result['field_sources']['max_temp_c']['status']=='FETCH_FAILED'
    assert result['min_temp_c']==23.4  # synthetic shared payload, tests source independence only


def test_source_daily_archive(monkeypatch):
    import dashboard
    text=(FIXTURES/'hko_daily_sample.json').read_text()
    monkeypatch.setattr(dashboard.httpx,'get',lambda url,**kw:httpx.Response(200,text=text,request=httpx.Request('GET',url)))
    report=dashboard._fetch_daily_weather(date(2026,10,1),{})
    assert report['mean_temp_c']==30.4
    assert report['rainfall_mm']==0


def test_model_safety_and_holdout(tmp_path,monkeypatch):
    import models_baseline as baseline
    import pandas as pd
    monkeypatch.setattr(baseline,'MODEL_PATH',tmp_path/'model.pkl')
    rows=[]
    for day in range(1,11):
        for horse in range(4):
            rows.append({'race_date':f'2026-10-{day:02d}','race_id':str(day),'is_win':int(horse==0),
                'starts_before':day-1,'win_sum_50':int(horse==0)*(day-1),'win_rate_50':float(horse==0),
                'final_win_odds':999, 'sectional_time':123, 'WeatherMeanTempC':30})
    baseline.train_baseline(pd.DataFrame(rows))
    report=json.loads((tmp_path/'model.evaluation.json').read_text())
    assert report['feature_columns']==baseline.SAFE_FEATURES
    assert not report['pre_race_verified']
    assert report['evaluation']['validation']['dates'][1]<report['evaluation']['test']['dates'][0]
    assert 'brier' in report['evaluation']['test']
    with pytest.raises(ValueError):
        baseline.train_baseline(pd.DataFrame(rows[:4]))


def test_same_day_history_is_excluded():
    import polars as pl
    from feature_engineering import compute_rolling_winrate
    df=pl.DataFrame({'horse_id':['a','a','a'],'race_date':['2026-10-01','2026-10-01','2026-10-02'],
        'race_id':['one','two','three'],'finishing_pos':[1,2,1]})
    result=compute_rolling_winrate(df).to_dicts()
    assert [r['starts_before'] for r in result]==[0,0,2]
    assert result[2]['win_rate_50']==.5


def test_api_refuses_completed_entries():
    from api import predict, PredictRequest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        predict(PredictRequest(race_id='completed'))
    assert exc.value.status_code==409


def test_validator_detects_duplicates(parsed):
    from validate_racing_data import validate_races
    assert not validate_races([parsed])['errors']
    assert validate_races([parsed,parsed])['errors']


def test_incremental_skip(parsed,tmp_path,monkeypatch):
    import scraper_incremental as scraper
    from persistence import atomic_json
    from datetime import datetime,timezone
    monkeypatch.setattr(scraper,'DATA_DIR',tmp_path)
    monkeypatch.setattr(scraper,'CHECKPOINT',tmp_path/'checkpoint.json')
    atomic_json(tmp_path/f"race_{parsed['race_id']}.json",parsed)
    atomic_json(scraper.CHECKPOINT,{'races':{parsed['race_id']:{'status':'SUCCESS','verified':True,'last_successful_fetch':datetime.now(timezone.utc).isoformat()}}})
    worker=scraper.IncrementalScraper(scraper.DEFAULT_BASE_URL,rate_limit=0)
    monkeypatch.setattr(worker,'list_recent_race_urls',lambda:[URL])
    monkeypatch.setattr(worker,'parse_and_save',lambda url:pytest.fail('Verified race re-fetched'))
    assert worker.run_incremental()==0
    worker.client.close()


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='Set TEST_DATABASE_URL to an isolated disposable PostgreSQL database')
def test_postgres_idempotence(parsed,tmp_path,request):
    from sqlalchemy import create_engine,text
    from ingest import ensure_schema,ingest
    from migrate import migrate
    from persistence import atomic_json
    import uuid
    schema='hkjc_test_'+uuid.uuid4().hex
    admin=create_engine(os.environ['TEST_DATABASE_URL'])
    with admin.begin() as conn:
        conn.exec_driver_sql('CREATE SCHEMA '+schema)
    engine=create_engine(os.environ['TEST_DATABASE_URL'],connect_args={'options':'-csearch_path='+schema})
    def cleanup():
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql('DROP SCHEMA '+schema+' CASCADE')
        admin.dispose()
    request.addfinalizer(cleanup)
    ensure_schema(engine); migrate(engine); migrate(engine)
    parsed['venue_weather']={'date':parsed['meeting_date'],'station':'Sha Tin','station_code':'SHA',
        'resolution':'daily','mean_temp_c':23.4,'source_url':'fixture://hko'}
    atomic_json(tmp_path/f"race_{parsed['race_id']}.json",parsed)
    assert ingest(engine,tmp_path)==1
    assert ingest(engine,tmp_path)==1
    with engine.connect() as conn:
        counts={table:conn.execute(text('SELECT COUNT(*) FROM '+table)).scalar() for table in
            ['races','runners','sectional_times','dividends','weather_observations','race_weather_matches','odds_history']}
        assert counts['races']==1 and counts['runners']==14
        assert counts['sectional_times']==sum(len(r['sectionals']) for r in parsed['runners'])
        assert counts['dividends']==len(parsed['dividends'])
        assert counts['weather_observations']==1 and counts['race_weather_matches']==1
        assert counts['odds_history']==0
    from validate_racing_data import validate_database
    assert validate_database([parsed],engine)['status']=='PASSED'
    parsed['runners'][0]['finishing_time']=None
    parsed['venue_weather']=None
    atomic_json(tmp_path/f"race_{parsed['race_id']}.json",parsed)
    ingest(engine,tmp_path)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT finishing_time FROM runners WHERE runner_id=:id'),{'id':parsed['runners'][0]['runner_id']}).scalar()==pytest.approx(69.18)
    from race_weather import RaceWeatherProvider,enrich_race_time_weather
    from test_race_time_weather import source_transport
    timed_provider=RaceWeatherProvider(tmp_path/'timed_cache',httpx.Client(transport=httpx.MockTransport(source_transport)),rate_limit=0)
    try:
        parsed=enrich_race_time_weather(parsed,timed_provider)
    finally:
        timed_provider.close()
    atomic_json(tmp_path/f"race_{parsed['race_id']}.json",parsed)
    ingest(engine,tmp_path);ingest(engine,tmp_path)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM weather_observations WHERE observed_at IS NOT NULL')).scalar()==5
        assert conn.execute(text('SELECT COUNT(*) FROM race_weather_matches WHERE target_at IS NOT NULL')).scalar()==5
        assert conn.execute(text("SELECT source_payload->>'value' FROM weather_observations WHERE field_name='temperature_c'")).scalar()=='23.8'
    engine.dispose()


def test_weather_refresh_retains_field_provenance(tmp_path):
    from persistence import save_race
    path=tmp_path/'race.json'
    original={'race_id':'one','venue_weather':{'mean_temp_c':23.4,'field_sources':{'mean_temp_c':{'status':'AVAILABLE','source_url':'original'}}}}
    save_race(path,original)
    fresh={'race_id':'one','venue_weather':{'mean_temp_c':None,'field_sources':{'mean_temp_c':{'status':'FETCH_FAILED','source_url':'failed'}}}}
    result=save_race(path,fresh)
    assert result['venue_weather']['mean_temp_c']==23.4
    assert result['venue_weather']['field_sources']['mean_temp_c']['source_url']=='original'


def test_smaller_roster_cannot_look_complete(parsed,tmp_path):
    from persistence import save_race
    from validate_racing_data import validate_races
    path=tmp_path/'race.json'
    save_race(path,parsed)
    parsed['runners']=parsed['runners'][:-1]
    result=save_race(path,parsed)
    assert len(result['runners'])==14
    assert result['roster_refresh_status']=='MISMATCH_PRESERVED_HISTORY'
    assert validate_races([result])['errors']


def test_timed_weather_csv_and_json_exports(parsed,tmp_path,monkeypatch):
    import dashboard
    from race_weather import RaceWeatherProvider,enrich_race_time_weather
    from test_race_time_weather import source_transport
    provider=RaceWeatherProvider(tmp_path/'cache',httpx.Client(transport=httpx.MockTransport(source_transport)),rate_limit=0)
    try:
        enriched=enrich_race_time_weather(parsed,provider)
    finally:
        provider.close()
    monkeypatch.setattr(dashboard,'OUTPUT_DIR',tmp_path)
    names=dashboard._save_exports([enriched],'hkjc_timed','both')
    import pandas as pd
    frame=pd.read_csv(tmp_path/names[0])
    assert frame.iloc[0]['RaceTimeTempC']==23.8
    assert frame.iloc[0]['RaceTimeWeatherTargetKind']=='SCHEDULED'
    assert frame.iloc[0]['RaceTimeWeatherResolution']=='mixed'
    provenance=json.loads(frame.iloc[0]['RaceTimeWeatherProvenance'])
    assert provenance['temperature_c']['interval_minutes']==1
    assert provenance['rainfall_mm']['status']=='ARCHIVE_UNAVAILABLE'
    assert json.loads((tmp_path/names[1]).read_text())[0]['race_time_weather']['humidity_pct']==92
