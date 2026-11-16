"""Inventory a scraper installation; optionally back up files and PostgreSQL."""
import argparse
import hashlib
import json
import shutil
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from persistence import atomic_json


def inventory(root):
    files=[]
    for pattern in ('data/race_*.json','output/hkjc_*','mappings/entity_mappings.json','checkpoint.json'):
        for path in sorted(root.glob(pattern)):
            if path.is_file() and not path.is_symlink():
                files.append({'path':str(path.relative_to(root)),'bytes':path.stat().st_size,
                    'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'generated_at':datetime.now(timezone.utc).isoformat(), 'files':files,
            'free_bytes':shutil.disk_usage(root).free, 'database':'NOT_INSPECTED'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).parent)
    parser.add_argument('--report',type=Path,default=Path('output/inventory.json'))
    parser.add_argument('--backup-dir',type=Path)
    parser.add_argument('--database',action='store_true',help='Read DB row counts and schema; backup uses pg_dump via environment settings')
    args=parser.parse_args()
    report=inventory(args.root)
    if args.backup_dir:
        # Refuse to overwrite an earlier backup.
        args.backup_dir.mkdir(parents=True,exist_ok=False)
        for info in report['files']:
            destination=args.backup_dir/info['path']
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(args.root/info['path'],destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest()!=info['sha256']:
                raise RuntimeError('Backup verification failed: '+info['path'])
        report['backup_verified']=True
    if args.database:
        from db import get_engine
        from sqlalchemy import inspect, text
        engine=get_engine()
        tables=inspect(engine).get_table_names()
        with engine.connect() as conn:
            report['database']={name:conn.execute(text('SELECT COUNT(*) FROM '+engine.dialect.identifier_preparer.quote(name))).scalar() for name in tables}
            report['database_columns']={name:[c['name'] for c in inspect(engine).get_columns(name)] for name in tables}
        if args.backup_dir:
            # PGPASSWORD/PGHOST/PGPORT/PGDATABASE configure pg_dump without printing credentials.
            destination=args.backup_dir/'postgres.dump'
            url=engine.url
            environment=dict(os.environ)
            if url.password:
                environment['PGPASSWORD']=url.password
            command=['pg_dump','--format=custom','--file',str(destination),
                     '--host',url.host or 'localhost','--port',str(url.port or 5432),
                     '--username',url.username or '', '--dbname',url.database or '']
            subprocess.run(command,check=True,env=environment)
            subprocess.run(['pg_restore','--list',str(destination)],check=True,stdout=subprocess.DEVNULL)
            report['database_backup_archive_verified']=True
            report['database_restore_tested']=False
    atomic_json(args.report,report)
    if args.backup_dir:
        atomic_json(args.backup_dir/'inventory.json',report)
    print(f"Inventoried {len(report['files'])} files; DB: {'inspected' if args.database else 'not configured for this run'}")


if __name__=='__main__':
    main()
