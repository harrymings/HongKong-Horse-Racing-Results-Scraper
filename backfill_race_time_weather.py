"""Opt-in scheduled-time and archived-weather backfill. Preview by default; --apply verifies file backups."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
from race_weather import RaceWeatherProvider, enrich_race_time_weather
from persistence import atomic_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('data'))
    parser.add_argument('--date',help='Restrict to one YYYY-MM-DD meeting date')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup-dir',type=Path)
    parser.add_argument('--max-age-minutes',type=float,default=30)
    parser.add_argument('--report',type=Path,default=Path('output/race_time_weather_report.json'))
    args=parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error('--apply requires a new --backup-dir')
    files=[]
    for path in sorted(args.data_dir.glob('race_*.json')):
        race=json.loads(path.read_text(encoding='utf-8'))
        if not args.date or race.get('meeting_date') == args.date:
            files.append((path,race))
    if args.apply:
        args.backup_dir.mkdir(parents=True,exist_ok=False)
        for path,_ in files:
            destination=args.backup_dir/path.name
            shutil.copy2(path,destination)
            if hashlib.sha256(destination.read_bytes()).digest()!=hashlib.sha256(path.read_bytes()).digest():
                raise RuntimeError('Backup verification failed')
    if args.apply:
        from dashboard import OUTPUT_DIR
        export_backup=args.backup_dir/'exports'
        export_backup.mkdir()
        for source in OUTPUT_DIR.glob('hkjc_*'):
            if source.is_file() and not source.is_symlink() and source.suffix in {'.csv','.json'}:
                destination=export_backup/source.name
                shutil.copy2(source,destination)
                if hashlib.sha256(destination.read_bytes()).digest()!=hashlib.sha256(source.read_bytes()).digest():
                    raise RuntimeError('Export backup verification failed')
    provider=RaceWeatherProvider()
    results=[]
    try:
        for path,race in files:
            enriched=enrich_race_time_weather(race,provider,path if args.apply else None,args.max_age_minutes)
            results.append({'race_id':race['race_id'],'race_number':race.get('race_number'),
                'scheduled_start_at_hkt':enriched.get('scheduled_start_at_hkt'),
                'race_time_weather':enriched['race_time_weather']})
    finally:
        provider.close()
    atomic_json(args.report,{'applied':args.apply,'generated_at':datetime.now(timezone.utc).isoformat(),'races':results})
    # Regenerate scoped existing exports only on explicit apply; collection metadata is retained.
    if args.apply:
        from backfill_venue_weather import VENUE_TO_CODE
        from dashboard import OUTPUT_DIR, _save_exports
        import re
        all_races=[json.loads(path.read_text()) for path in args.data_dir.glob('race_*.json')]
        for path in list(OUTPUT_DIR.glob('hkjc_*.csv'))+list(OUTPUT_DIR.glob('hkjc_*.json')):
            match=re.fullmatch(r'hkjc_([a-z-]+)_(\d{4}-\d{2}-\d{2})(?:_to_(\d{4}-\d{2}-\d{2}))?',path.stem)
            if not match:
                continue
            courses={c.upper() for c in match[1].split('-')}
            scoped=[r for r in all_races if match[2]<=r['meeting_date']<=(match[3] or match[2]) and VENUE_TO_CODE.get(r.get('venue')) in courses]
            if scoped:
                _save_exports(scoped,path.stem,'csv' if path.suffix=='.csv' else 'json')
    print(f"{'Updated' if args.apply else 'Previewed'} {len(results)} races; see {args.report}")


if __name__=='__main__':
    main()
