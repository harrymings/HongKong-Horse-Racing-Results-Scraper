import json
from pathlib import Path
from db import get_engine, init_db
from sqlalchemy import text
import os

DATA_DIR = Path(__file__).parent / "data"
MAPPINGS = Path(__file__).parent / "mappings" / "entity_mappings.json"
SCHEMA_SQL = Path(__file__).parent / "schema.sql"


def load_mappings():
    if MAPPINGS.exists():
        return json.loads(MAPPINGS.read_text())
    return {}


def ensure_schema():
    if not SCHEMA_SQL.exists():
        print("schema.sql not found; skipping schema init")
        return
    sql = SCHEMA_SQL.read_text()
    eng = get_engine()
    try:
        init_db(sql, engine=eng)
        print("Schema ensured")
    except Exception as e:
        print("Failed to init schema:", e)
        raise


def ingest():
    eng = get_engine()
    mappings = load_mappings()
    files = list(DATA_DIR.glob("race_*.json"))
    if not files:
        print("No race JSON files found in data/; run scraper first")
        return 0

    ensure_schema()

    inserted_races = 0
    with eng.begin() as conn:
        for f in files:
            j = json.loads(f.read_text())
            race_id = j.get("race_id")
            source_url = j.get("source_url")
            scraped_at = j.get("scraped_at")
            checksum = j.get("race_id")
            # Insert race (idempotent)
            q = text(
                "INSERT INTO races(race_id, source_url, checksum, scraped_at) VALUES(:race_id, :source_url, :checksum, :scraped_at) ON CONFLICT (race_id) DO NOTHING"
            )
            conn.execute(q, {"race_id": race_id, "source_url": source_url, "checksum": checksum, "scraped_at": scraped_at})
            inserted_races += 1

            for r in j.get("runners", []):
                # ensure canonical entities
                for ent_type, table in [("horse", "horses"), ("jockey", "jockeys"), ("trainer", "trainers")]:
                    cid = r.get(f"{ent_type}_id")
                    if not cid:
                        continue
                    info = mappings.get(cid, {})
                    name = info.get("canonical_name") or r.get(f"{ent_type}_name") if r.get(f"{ent_type}_name") else None
                    if name:
                        q_ent = text(f"INSERT INTO {table}({ent_type}_id, name) VALUES(:id, :name) ON CONFLICT ({ent_type}_id) DO NOTHING")
                        conn.execute(q_ent, {"id": cid, "name": name})

                # insert runner
                q_runner = text(
                    "INSERT INTO runners(runner_id, race_id, horse_id, trainer_id, jockey_id, created_at) VALUES(:runner_id, :race_id, :horse_id, :trainer_id, :jockey_id, now()) ON CONFLICT (runner_id) DO NOTHING"
                )
                conn.execute(
                    q_runner,
                    {
                        "runner_id": r.get("runner_id"),
                        "race_id": race_id,
                        "horse_id": r.get("horse_id"),
                        "trainer_id": r.get("trainer_id"),
                        "jockey_id": r.get("jockey_id"),
                    },
                )

    print(f"Ingested {inserted_races} races")
    return inserted_races


if __name__ == '__main__':
    try:
        result = ingest()
        raise SystemExit(0 if result else 2)
    except Exception as e:
        print("Ingestion failed:", e)
        raise SystemExit(1)
