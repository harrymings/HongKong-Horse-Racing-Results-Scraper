"""Read-only validation of collected races. Exit 1 on blocking errors, 2 on no data."""
import argparse
import json
from pathlib import Path
from persistence import atomic_json, atomic_text


def validate_races(races):
    errors, warnings = [], []
    race_keys, runner_ids = set(), set()
    runner_count = sections = dividends = 0
    null_counts = {field:0 for field in ['horse_name','horse_code','draw','actual_weight','declared_horse_weight','finishing_pos','finishing_time','win_odds']}
    for race in races:
        rid=race.get('race_id')
        key=(race.get('meeting_date'),race.get('venue_code') or race.get('venue'),race.get('race_number'))
        def error(message):
            errors.append({'race_id':rid,'message':message})
        if key in race_keys:
            error('Duplicate meeting/venue/race number')
        race_keys.add(key)
        if not rid or not all(key):
            error('Missing race identity')
        if race.get('roster_refresh_status') == 'MISMATCH_PRESERVED_HISTORY':
            error('Refreshed roster is smaller; historical runners retained pending reconciliation')
        abandoned = race.get('race_status') == 'ABANDONED' and bool(race.get('abandonment_text'))
        if not abandoned and (not race.get('distance_m') or not race.get('grade')):
            error('Missing distance or class')
        if not race.get('source_url'):
            error('Missing source URL')
        horses=set()
        winners=0
        for r in race.get('runners',[]):
            runner_count+=1
            for field in null_counts:
                null_counts[field] += r.get(field) is None
            if r.get('horse_no') in horses:
                error('Duplicate horse number')
            horses.add(r.get('horse_no'))
            if not r.get('horse_name') or (not r.get('horse_no') and not (r.get('finish_status') == 'SCRATCHED' and r.get('horse_code'))) or not r.get('runner_id'):
                error('Missing runner identity')
            if r.get('runner_id') in runner_ids:
                error('Duplicate runner ID')
            runner_ids.add(r.get('runner_id'))
            if not r.get('horse_code'):
                warnings.append({'race_id':rid,'runner_id':r.get('runner_id'),'message':'Horse code missing; fallback identity'})
            if r.get('finishing_pos')==1:
                winners+=1
            if r.get('finish_status','FINISHED')=='FINISHED' and (not r.get('finishing_time') or r['finishing_time']<=0):
                error('Finished runner lacks positive finish time')
            values=r.get('sectionals',[])
            sections+=len(values)
            if r.get('sectional_status')=='VALID':
                times=[s.get('section_time_seconds') for s in values]
                if not times or any(t is None for t in times) or not r.get('finishing_time') or abs(sum(times)-r['finishing_time']) > .100001:
                    error('Verified sectionals fail finish-time tolerance')
            else:
                warnings.append({'race_id':rid,'runner_id':r.get('runner_id'),'message':'Sectionals unverified: '+str(r.get('sectional_status'))})
        if not horses and not abandoned:
            error('Empty runner roster')
        if not winners and not abandoned:
            error('No winner in published results')
        if winners>1:
            warnings.append({'race_id':rid,'message':'Multiple winners; official dead-heat evidence needs review'})
        dividends+=len(race.get('dividends',[]))
        for key in ('weather','venue_weather'):
            w=race.get(key)
            if w:
                if w.get('date')!=race.get('meeting_date'):
                    error('Weather observation date mismatch')
                if w.get('resolution','daily')=='daily' and w.get('is_pre_race_available'):
                    error('Same-day daily weather incorrectly marked pre-race')
                if not w.get('station'):
                    error('Missing weather station')
        timed=race.get('race_time_weather') or {}
        target=timed.get('target_at')
        if timed.get('pre_race_verified'):
            error('Pre-race pipeline is not verified; timed weather cannot certify it')
        for field, observation in timed.get('fields',{}).items():
            if observation.get('status') != 'AVAILABLE':
                continue
            try:
                from race_weather import aware
                end=aware(observation['observation_period_end'])
                start=aware(observation['observation_period_start'])
                known=aware(observation['known_at'])
                cutoff=aware(target)
                if end>cutoff or known>cutoff or start>=end:
                    error('Timed weather interval/public availability exceeds cutoff: '+field)
                if observation.get('station')!=race.get('venue'):
                    error('Timed weather station mismatch: '+field)
                if (cutoff-end).total_seconds()>timed.get('max_age_minutes',30)*60:
                    error('Stale timed weather: '+field)
                if timed.get(field)!=observation.get('value'):
                    error('Timed weather value/provenance mismatch: '+field)
                expected_resolution='hourly' if observation.get('interval_minutes')==60 else 'subhourly'
                if observation.get('resolution')!=expected_resolution:
                    error('Incorrect timed weather resolution: '+field)
            except (ValueError,KeyError,TypeError):
                error('Malformed timed weather provenance: '+field)
    return {'status':'FAILED' if errors else ('NO_DATA' if not races else 'PASSED'),
            'counts':{'dates':len({r.get('meeting_date') for r in races}),'races':len(races),
                      'runners':runner_count,'sectionals':sections,'dividends':dividends},
            'null_counts':null_counts, 'null_share':{field:count/runner_count if runner_count else None for field,count in null_counts.items()},
            'errors':errors,'warnings':warnings,
            'coverage_status':'UNVERIFIED_WITHOUT_MEETING_MANIFEST', 'database_parity':'NOT_CHECKED'}



def validate_database(races, engine):
    """Compare included race records; do not mistake unrelated DB history for duplicates."""
    from sqlalchemy import text
    errors=[]
    counts={name:0 for name in ['races','runners','sectionals','dividends']}
    with engine.connect() as conn:
        for race in races:
            rid=race['race_id']
            expected={'races':1,'runners':len(race.get('runners',[])),
                      'sectionals':sum(len(r.get('sectionals',[])) for r in race.get('runners',[])),
                      'dividends':len(race.get('dividends',[]))}
            for label,table in [('races','races'),('runners','runners'),('sectionals','sectional_times'),('dividends','dividends')]:
                observed=conn.execute(text(f'SELECT COUNT(*) FROM {table} WHERE race_id=:id'),{'id':rid}).scalar()
                counts[label]+=observed
                if observed!=expected[label]:
                    errors.append({'race_id':rid,'message':f'DB parity {label}: JSON={expected[label]}, DB={observed}'})
            rows=conn.execute(text('SELECT runner_id,horse_id,finishing_time,win_odds FROM runners WHERE race_id=:id'),{'id':rid}).mappings()
            source={r['runner_id']:r for r in race.get('runners',[])}
            for row in rows:
                runner=source.get(row['runner_id'])
                if runner:
                    for field in ['horse_id','finishing_time','win_odds']:
                        value=runner.get(field)
                        db_value=row[field]
                        same=(abs(value-db_value)<.0001 if isinstance(value,(float,int)) and db_value is not None else value==db_value)
                        if value is not None and not same:
                            errors.append({'race_id':rid,'runner_id':row['runner_id'],'message':'DB value mismatch: '+field})
        for race in races:
            timed=race.get('race_time_weather') or {}
            if not timed.get('target_at'):
                continue
            expected={field:observation for field,observation in timed.get('fields',{}).items() if observation.get('status')=='AVAILABLE'}
            observed=list(conn.execute(text("""SELECT w.field_name,w.source_payload FROM weather_observations w
                JOIN race_weather_matches m ON m.observation_id=w.id
                WHERE m.race_id=:id AND m.target_at=:target AND w.field_name IS NOT NULL"""),
                {'id':race['race_id'],'target':timed['target_at']}).mappings())
            if len(observed)!=len(expected):
                errors.append({'race_id':race['race_id'],'message':'Timed weather DB count mismatch'})
            for row in observed:
                field=row['field_name']; source=expected.get(field,{})
                if row['source_payload']!=source:
                    errors.append({'race_id':race['race_id'],'message':'Timed weather DB provenance mismatch: '+field})
        orphan=conn.execute(text('SELECT COUNT(*) FROM runners r LEFT JOIN races t ON r.race_id=t.race_id WHERE t.race_id IS NULL')).scalar()
        if orphan:
            errors.append({'message':f'{orphan} orphan runner records'})
    return {'status':'FAILED' if errors else 'PASSED','counts':counts,'errors':errors}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('data'))
    parser.add_argument('--report-dir',type=Path,default=Path('output'))
    parser.add_argument('--database',action='store_true',help='Check parity against configured PostgreSQL (read-only)')
    args=parser.parse_args()
    races=[]; unreadable=[]
    for path in sorted(args.data_dir.glob('race_*.json')):
        try:
            races.append(json.loads(path.read_text(encoding='utf-8')))
        except (ValueError,OSError) as exc:
            unreadable.append({'file':str(path),'message':str(exc)})
    report=validate_races(races)
    if args.database:
        from db import get_engine
        try:
            report['database_parity']=validate_database(races,get_engine())
            report['errors'].extend(report['database_parity']['errors'])
        except Exception as exc:
            report['database_parity']='FAILED'
            report['errors'].append({'message':'DB parity unavailable: '+str(type(exc).__name__)})
    report['errors'].extend(unreadable)
    if report['errors']:
        report['status']='FAILED'
    atomic_json(args.report_dir/'validation_report.json',report)
    content=f"# Racing data validation\n\nStatus: {report['status']}\n\nCounts: {report['counts']}\n\nCoverage: {report['coverage_status']}\n\nDB parity: {report['database_parity']}\n"
    for label in ('errors','warnings'):
        content+='\n## '+label.title()+'\n\n'+('\n'.join('- '+json.dumps(e) for e in report[label]) or 'None')+'\n'
    atomic_text(args.report_dir/'validation_report.md',content)
    print(json.dumps({k:report[k] for k in ('status','counts','coverage_status')}))
    return 1 if report['errors'] else 0 if races else 2


if __name__=='__main__':
    raise SystemExit(main())
