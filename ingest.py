import json
import hashlib
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

    processed_races = 0
    with eng.begin() as conn:
        for f in files:
            j = json.loads(f.read_text())
            race_id = j.get("race_id")
            meeting_date = j.get("meeting_date")
            if not race_id or not meeting_date:
                raise ValueError(f"{f.name} is missing race_id or meeting_date")
            source_url = j.get("source_url")
            scraped_at = j.get("scraped_at")
            content = {key: value for key, value in j.items() if key != "scraped_at"}
            checksum = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
            q = text(
                "INSERT INTO races(race_id, meeting_date, meeting_name, venue, race_number, distance_m, grade, going, source_url, checksum, scraped_at) "
                "VALUES(:race_id, :meeting_date, :meeting_name, :venue, :race_number, :distance_m, :grade, :going, :source_url, :checksum, :scraped_at) "
                "ON CONFLICT (race_id) DO UPDATE SET meeting_date = EXCLUDED.meeting_date, meeting_name = EXCLUDED.meeting_name, "
                "venue = EXCLUDED.venue, race_number = EXCLUDED.race_number, distance_m = EXCLUDED.distance_m, grade = EXCLUDED.grade, "
                "going = EXCLUDED.going, source_url = EXCLUDED.source_url, checksum = EXCLUDED.checksum, scraped_at = EXCLUDED.scraped_at"
            )
            conn.execute(q, {
                "race_id": race_id,
                "meeting_date": meeting_date,
                "meeting_name": j.get("race_name"),
                "venue": j.get("venue"),
                "race_number": j.get("race_number"),
                "distance_m": j.get("distance_m"),
                "grade": j.get("grade"),
                "going": j.get("going"),
                "source_url": source_url,
                "checksum": checksum,
                "scraped_at": scraped_at,
            })

            for r in j.get("runners", []):
                # ensure canonical entities
                for ent_type, table in [("horse", "horses"), ("jockey", "jockeys"), ("trainer", "trainers")]:
                    cid = r.get(f"{ent_type}_id")
                    if not cid:
                        continue
                    info = mappings.get(cid, {})
                    name = info.get("canonical_name") or r.get(f"{ent_type}_name")
                    if name:
                        q_ent = text(f"INSERT INTO {table}({ent_type}_id, name) VALUES(:id, :name) ON CONFLICT ({ent_type}_id) DO NOTHING")
                        conn.execute(q_ent, {"id": cid, "name": name})

                q_runner = text(
                    "INSERT INTO runners(runner_id, race_id, horse_id, trainer_id, jockey_id, draw, weight_carried, declared_weight, finishing_pos, finishing_time, margin, running_position, win_odds) "
                    "VALUES(:runner_id, :race_id, :horse_id, :trainer_id, :jockey_id, :draw, :weight_carried, :declared_weight, :finishing_pos, :finishing_time, :margin, :running_position, :win_odds) "
                    "ON CONFLICT (runner_id) DO UPDATE SET horse_id = EXCLUDED.horse_id, trainer_id = EXCLUDED.trainer_id, jockey_id = EXCLUDED.jockey_id, "
                    "draw = EXCLUDED.draw, weight_carried = EXCLUDED.weight_carried, declared_weight = EXCLUDED.declared_weight, finishing_pos = EXCLUDED.finishing_pos, "
                    "finishing_time = EXCLUDED.finishing_time, margin = EXCLUDED.margin, running_position = EXCLUDED.running_position, win_odds = EXCLUDED.win_odds"
                )
                conn.execute(
                    q_runner,
                    {
                        "runner_id": r.get("runner_id"),
                        "race_id": race_id,
                        "horse_id": r.get("horse_id"),
                        "trainer_id": r.get("trainer_id"),
                        "jockey_id": r.get("jockey_id"),
                        "draw": r.get("draw"),
                        "weight_carried": r.get("actual_weight"),
                        "declared_weight": r.get("declared_horse_weight"),
                        "finishing_pos": r.get("finishing_pos"),
                        "finishing_time": r.get("finishing_time"),
                        "margin": r.get("margin"),
                        "running_position": r.get("running_position"),
                        "win_odds": r.get("win_odds"),
                    },
                )
            processed_races += 1

    print(f"Processed {processed_races} races")
    return processed_races


if __name__ == '__main__':
    try:
        result = ingest()
        raise SystemExit(0 if result else 2)
    except Exception as e:
        print("Ingestion failed:", e)
        raise SystemExit(1)
