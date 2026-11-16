"""Reconcile source-code horse IDs offline. Default is a read-only proposal.

Use --apply --backup-dir NEW_DIRECTORY to verify backups before writing.
Name-only records remain unresolved; ambiguous legacy IDs may map to several codes.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
from scraper_incremental import _horse_code
from entity_resolution import map_entity
from persistence import atomic_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('data'))
    parser.add_argument('--report',type=Path,default=Path('output/identity_reconciliation.json'))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup-dir',type=Path)
    args=parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error('--apply requires --backup-dir pointing to a new directory')
    proposals=[]; records=[]; unresolved=[]
    for path in sorted(args.data_dir.glob('race_*.json')):
        race=json.loads(path.read_text(encoding='utf-8'))
        for runner in race.get('runners',[]):
            code=runner.get('horse_code') or _horse_code(runner.get('horse_name',''),runner.get('horse_url'))
            if not code:
                unresolved.append({'runner_id':runner.get('runner_id'),'reason':'No source horse code'})
                continue
            canonical=hashlib.sha1(f'horse:hkjc:{code.upper()}'.encode()).hexdigest()
            if runner.get('horse_id') != canonical:
                proposals.append({'runner_id':runner['runner_id'],'legacy_horse_id':runner.get('horse_id'),
                                  'horse_id':canonical,'horse_code':code})
                if args.apply:
                    runner['legacy_horse_id']=runner.get('legacy_horse_id') or runner.get('horse_id')
                    runner.update(horse_id=canonical,horse_code=code,identity_status='SOURCE_CODE')
        records.append((path,race))
    if args.apply:
        args.backup_dir.mkdir(parents=True,exist_ok=False)
        for path,_ in records:
            destination=args.backup_dir/path.name
            shutil.copy2(path,destination)
            if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(destination.read_bytes()).digest():
                raise RuntimeError('Backup verification failed')
        from entity_resolution import MAPPING_FILE
        if MAPPING_FILE.exists():
            shutil.copy2(MAPPING_FILE,args.backup_dir/'entity_mappings.json')
        for path,race in records:
            for runner in race.get('runners',[]):
                if runner.get('horse_code'):
                    map_entity('horse',runner['horse_name'],{'source_id':runner['horse_code']})
            atomic_json(path,race)
    atomic_json(args.report,{'applied':args.apply,'proposals':proposals,'unresolved':unresolved,
                             'notes':'No name-only merges; no race/runner IDs changed. Re-ingest after backed-up DB migration.'})
    print(f'{len(proposals)} identity changes; {len(unresolved)} unresolved source codes')


if __name__=='__main__':
    main()
