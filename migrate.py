"""Apply additive versioned SQL migrations, only after explicit backup acknowledgement."""
import argparse
from pathlib import Path
from sqlalchemy import text
from db import get_engine


def migrate(engine):
    with engine.begin() as conn:
        # Serialize concurrent migration runners.
        conn.execute(text('SELECT pg_advisory_xact_lock(71408231)'))
        conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)')
        applied = set(conn.execute(text('SELECT version FROM schema_migrations')).scalars())
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            if path.name not in applied:
                conn.exec_driver_sql(path.read_text())
                conn.execute(text('INSERT INTO schema_migrations(version) VALUES (:version)'), {'version':path.name})


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-verified',action='store_true',required=True,
                        help='Acknowledge a verified backup of the target DB and source data')
    parser.parse_args()
    migrate(get_engine())
