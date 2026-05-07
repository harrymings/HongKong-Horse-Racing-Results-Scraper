import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from typing import Optional

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://user:password@localhost:5432/hkracing")


def get_engine(database_url: Optional[str] = None) -> Engine:
    url = database_url or DATABASE_URL
    engine = create_engine(url, future=True)
    return engine


def init_db(schema_sql: str = None, engine: Optional[Engine] = None):
    eng = engine or get_engine()
    if schema_sql:
        with eng.begin() as conn:
            conn.execute(text(schema_sql))
    return eng


def execute_query(query: str, params: dict = None, engine: Optional[Engine] = None):
    eng = engine or get_engine()
    with eng.connect() as conn:
        res = conn.execute(text(query), params or {})
        try:
            return res.fetchall()
        except Exception:
            return None
