import json
from pathlib import Path
from datetime import date
import pytest
from bs4 import BeautifulSoup
import entity_resolution as entities
import scraper_incremental as scraper
from persistence import save_race

FIXTURES = Path(__file__).parent / 'fixtures'
URL = 'https://racing.hkjc.com/en-us/local/information/localresults?racedate=2026/11/14&Racecourse=ST&RaceNo=1'

@pytest.fixture
def parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(entities, 'MAPPING_FILE', tmp_path / 'entities.json')
    worker = scraper.IncrementalScraper(scraper.DEFAULT_BASE_URL, rate_limit=0)
    def fetch(url):
        name = 'sectionals' if 'displaysectionaltime' in url else 'results'
        return (FIXTURES / f'{name}_sha_tin_2026-11-14_r1.html').read_text()
    monkeypatch.setattr(worker, '_get', fetch)
    yield worker.parse_and_save(URL)
    worker.client.close()

def test_official_race(parsed):
    assert parsed['class_label'] == 'Class 4 (Restricted)'
    assert parsed['distance_m'] == 1200
    assert parsed['track_type'] == 'Turf'
    assert parsed['rail_position'] == 'A'
    assert len(parsed['runners']) == 14
    winner = parsed['runners'][0]
    assert (winner['horse_no'], winner['horse_code'], winner['horse_name']) == (12, 'L438', 'SECRET HEDGE')
    assert winner['finishing_time'] == 69.18
    assert winner['win_odds'] == 34
    assert winner['margin'] == 0
    assert winner['sectional_status'] == 'VALID'
    assert sum(s['section_time_seconds'] for s in winner['sectionals']) == pytest.approx(69.18)
    assert parsed['actual_start_at_hkt'] is None
    assert parsed['timezone'] == 'Asia/Hong_Kong'

@pytest.mark.parametrize('text,pos,expected', [('1/2',2,.5),('3/4',2,.75),('1-1/2',2,1.5),('3-3/4',2,3.75),('1 1/2',2,1.5),('---',1,0),('---',2,None),('-',None,None),('N',2,.05),('NK',2,.25),('1/0',2,None),('-1',2,None)])
def test_margins(text,pos,expected):
    assert scraper._margin_in_lengths(text,pos) == expected

def test_non_runner_table_and_reorder():
    soup = BeautifulSoup((FIXTURES/'results_sha_tin_2026-11-14_r1.html').read_text(),'lxml')
    soup.append(BeautifulSoup('<table><tr>'+''.join('<td>12</td>' for _ in range(14))+'</tr></table>','lxml'))
    table = soup.select_one('.performance table')
    # Synthetic column reorder of the actual source table.
    for row in table.select('tr'):
        cells = row.find_all(['td','th'],recursive=False)
        if len(cells)==12:
            row.insert(0,cells[-1].extract())
    assert len(list(scraper._result_rows(soup))) == 14

def test_duplicate_and_missing_table():
    soup = BeautifulSoup((FIXTURES/'results_sha_tin_2026-11-14_r1.html').read_text(),'lxml')
    tbody = soup.select_one('.performance tbody')
    import copy
    tbody.append(copy.copy(tbody.select_one('tr')))
    with pytest.raises(scraper.RaceParseError,match='Duplicate'):
        list(scraper._result_rows(soup))
    with pytest.raises(scraper.RaceParseError):
        list(scraper._result_rows(BeautifulSoup('<table></table>','lxml')))

def test_sectionals_race_scope():
    soup = BeautifulSoup((FIXTURES/'sectionals_sha_tin_2026-11-14_r1.html').read_text(),'lxml')
    with pytest.raises(scraper.RaceParseError):
        scraper._parse_sectionals(soup,2)
    import copy
    second=copy.copy(soup.select_one('#Race1')); second['id']='Race2'
    second.select_one('tbody tr td:nth-child(4) p:last-child').string='99.00'
    soup.append(second)
    assert scraper._parse_sectionals(soup,1)[12]['sections'][0]['section_time_seconds'] == 24.43
    assert scraper._parse_sectionals(soup,2)[12]['sections'][0]['section_time_seconds'] == 99

def test_identity(tmp_path,monkeypatch):
    monkeypatch.setattr(entities,'MAPPING_FILE',tmp_path/'mapping.json')
    a=entities.map_entity('horse','Same',{'source_id':'L438'})
    b=entities.map_entity('horse','Other name',{'source_id':'L438'})
    c=entities.map_entity('horse','Same',{'source_id':'M046'})
    fallback=entities.map_entity('horse','Same')
    assert a['canonical_id']==b['canonical_id']
    assert a['canonical_id']!=c['canonical_id']!=fallback['canonical_id']

def test_preserve_enrichment(tmp_path,parsed):
    path=tmp_path/'race.json'
    parsed['weather']={'mean_temp_c':23.4}
    save_race(path,parsed)
    parsed['weather']=None
    parsed['runners'][0]['sectionals']=[]
    merged=save_race(path,parsed)
    assert merged['weather']['mean_temp_c']==23.4
    assert len(merged['runners'][0]['sectionals'])==3
    assert save_race(path,parsed)==merged

def test_weather_before_ryes(monkeypatch):
    import weather
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        import httpx
        name = 'hko_clmmaxt_sample.json' if 'CLMMAXT' in url else 'hko_clmmint_sample.json' if 'CLMMINT' in url else 'hko_clmtemp_sample.json'
        payload=json.loads((FIXTURES/name).read_text())
        return httpx.Response(200,json=payload,request=httpx.Request('GET',url))
    monkeypatch.setattr(weather.httpx,'get',get)
    result=weather.fetch_venue_weather('Sha Tin',date(2018,11,14),{}, {})
    assert result['mean_temp_c']==23.4
    assert result['max_temp_c']==25.7
    assert result['min_temp_c']==22.4
    assert result['resolution']=='daily'
    assert not result['is_pre_race_available']
    assert len(calls)==3
    assert not any('RYES' in url for url in calls)

def test_partial_status(monkeypatch,tmp_path):
    import dashboard
    monkeypatch.setattr(dashboard,'JOB_FILE',tmp_path/'job.json')
    dashboard.cancel_event.clear()
    dashboard.job.update(expected=0,successful=0,races=[],log=[],error=None)
    class Worker:
        rate_limit=0
        def list_recent_race_urls(self):
            return [URL,URL.replace('RaceNo=1','RaceNo=2')]
        def parse_and_save(self,url):
            if 'RaceNo=2' in url:
                raise scraper.RaceParseError('fixture failure')
            return {'race_id':'one','runners':[]}
    races=dashboard.scrape_date(Worker(),'14/11/2026',['ST'])
    assert len(races)==1
    assert dashboard._job_state()=='PARTIAL'
    dashboard.job['state']=dashboard._job_state()
    response=dashboard.app.test_client().get('/status').json
    assert response['state']=='PARTIAL'
    assert response['successful']==1 and response['expected']==2
    assert response['races'][1]['status']=='FAILED_PARSE'


@pytest.mark.parametrize('label,distance', [('Class 5',1000),('Class 4 (Restricted)',1200),('Class 3',1400),('Group 1',1600),('Class 5',1650),('Group 2',1800),('Group 3',2000),('Class 2',2200),('G1',2400)])
def test_synthetic_header_variants(label,distance):
    # Mutations of the source header; these do not claim new official race fixtures.
    html=(FIXTURES/'results_sha_tin_2026-11-14_r1.html').read_text()
    html=html.replace('Class 4 (Restricted) - 1200M - (60-40)',f'{label} – {distance}M')
    metadata=scraper._race_metadata(BeautifulSoup(html,'lxml'),1)
    assert metadata['class_label']==label
    assert metadata['distance_m']==distance


@pytest.mark.parametrize('mode,expected', [('date','RACE_MISMATCH'),('horse','RACE_MISMATCH'),('time','TIME_MISMATCH'),('fetch','FETCH_FAILED'),('layout','PARSE_FAILED'),('empty','MISSING_AT_SOURCE')])
def test_sectional_failure_classification(tmp_path,monkeypatch,mode,expected):
    monkeypatch.setattr(scraper,'DATA_DIR',tmp_path)
    monkeypatch.setattr(entities,'MAPPING_FILE',tmp_path/'entities.json')
    html=(FIXTURES/'sectionals_sha_tin_2026-11-14_r1.html').read_text()
    if mode=='date':
        html=html.replace('14/11/2026','11/11/2026')
    if mode=='horse':
        html=html.replace('L438','M999')
    if mode=='time':
        html=html.replace('24.43','24.63')
    if mode in {'layout','empty'}:
        soup=BeautifulSoup(html,'lxml')
        if mode=='layout':
            soup.select_one('#Race1')['id']='Race2'
        else:
            soup.select_one('#Race1 tbody').clear()
        html=str(soup)
    worker=scraper.IncrementalScraper(scraper.DEFAULT_BASE_URL,rate_limit=0)
    def fetch(url):
        if 'displaysectionaltime' in url:
            if mode=='fetch':
                raise RuntimeError('synthetic upstream failure')
            return html
        return (FIXTURES/'results_sha_tin_2026-11-14_r1.html').read_text()
    monkeypatch.setattr(worker,'_get',fetch)
    result=worker.parse_and_save(URL)
    winner=result['runners'][0]
    assert winner['sectional_status']==expected
    if mode in {'date','horse','fetch','layout'}:
        assert winner['sectionals']==[]
    worker.client.close()


@pytest.mark.parametrize('number',[3,6])
def test_official_withdrawal_and_abandonment(tmp_path,monkeypatch,number):
    monkeypatch.setattr(scraper,'DATA_DIR',tmp_path)
    monkeypatch.setattr(entities,'MAPPING_FILE',tmp_path/'entities.json')
    worker=scraper.IncrementalScraper(scraper.DEFAULT_BASE_URL,rate_limit=0)
    def fetch(url):
        if 'displaysectionaltime' in url:
            raise RuntimeError('Sectionals not included in this result fixture')
        return (FIXTURES/f'results_sha_tin_2026-11-14_r{number}.html').read_text()
    monkeypatch.setattr(worker,'_get',fetch)
    result=worker.parse_and_save(URL.replace('RaceNo=1',f'RaceNo={number}'))
    from validate_racing_data import validate_races
    assert not validate_races([result])['errors']
    if number==3:
        withdrawn=[r for r in result['runners'] if r['finish_status']=='SCRATCHED']
        assert {r['horse_code'] for r in withdrawn}=={'K399','L060'}
        assert next(r for r in withdrawn if r['horse_code']=='K399')['horse_no'] is None
    else:
        assert result['race_status']=='ABANDONED'
        assert result['runners']==[]
    worker.client.close()


def test_listing_includes_displayed_race_without_self_link(monkeypatch):
    worker=scraper.IncrementalScraper(scraper.DEFAULT_BASE_URL+'?racedate=2026/11/14',rate_limit=0)
    html=(FIXTURES/'results_sha_tin_2026-11-14_r1.html').read_text()
    html+='<a href="?racedate=2026/11/14&amp;Racecourse=ST&amp;RaceNo=2">Race 2</a>'
    monkeypatch.setattr(worker,'_get',lambda url:html)
    assert [scraper._race_identity(url)[3] for url in worker.list_recent_race_urls()]==[1,2]
    worker.client.close()
