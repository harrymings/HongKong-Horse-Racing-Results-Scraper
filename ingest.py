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


def ensure_schema(engine=None):
    if not SCHEMA_SQL.exists():
        print("schema.sql not found; skipping schema init")
        return
    sql = SCHEMA_SQL.read_text()
    eng = engine or get_engine()
    try:
        init_db(sql, engine=eng)
        print("Schema ensured")
    except Exception as e:
        print("Failed to init schema:", e)
        raise


def ingest(engine=None, data_dir=None):
    eng = engine or get_engine()
    mappings = load_mappings()
    files = sorted((Path(data_dir) if data_dir else DATA_DIR).glob("race_*.json"))
    if not files:
        print("No race JSON files found in data/; run scraper first")
        return 0

    records=[json.loads(f.read_text(encoding='utf-8')) for f in files]
    ids=[r.get('race_id') for r in records]
    keys=[(r.get('meeting_date'), r.get('venue_code') or r.get('venue'), r.get('race_number')) for r in records]
    if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
        raise ValueError('Duplicate race identities in input files; resolve before ingestion')
    for record in records:
        numbers=[r.get('horse_no') for r in record.get('runners',[]) if r.get('horse_no') is not None]
        if len(numbers)!=len(set(numbers)):
            raise ValueError('Duplicate horse numbers in race '+str(record.get('race_id')))
    ensure_schema(eng)
    # Migrations are a separate explicit command with backup acknowledgement.
    from sqlalchemy import inspect
    if "source_payload" not in {c["name"] for c in inspect(eng).get_columns("races")}:
        raise RuntimeError("Run migrate.py --backup-verified against a backed-up DB before ingestion")

    if any(r.get('race_time_weather',{}).get('fields') for r in records):
        if 'observed_at' not in {c['name'] for c in inspect(eng).get_columns('weather_observations')}:
            raise RuntimeError('Run migrate.py --backup-verified to apply timed-weather migration 002')
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
                "ON CONFLICT (race_id) DO UPDATE SET meeting_date = COALESCE(EXCLUDED.meeting_date, races.meeting_date), meeting_name = COALESCE(EXCLUDED.meeting_name, races.meeting_name), "
                "venue = COALESCE(EXCLUDED.venue, races.venue), race_number = COALESCE(EXCLUDED.race_number, races.race_number), distance_m = COALESCE(EXCLUDED.distance_m, races.distance_m), grade = COALESCE(EXCLUDED.grade, races.grade), "
                "going = COALESCE(EXCLUDED.going, races.going), source_url = COALESCE(EXCLUDED.source_url, races.source_url), checksum = COALESCE(EXCLUDED.checksum, races.checksum), scraped_at = COALESCE(EXCLUDED.scraped_at, races.scraped_at)"
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
                    "ON CONFLICT (runner_id) DO UPDATE SET horse_id = COALESCE(EXCLUDED.horse_id, runners.horse_id), trainer_id = COALESCE(EXCLUDED.trainer_id, runners.trainer_id), jockey_id = COALESCE(EXCLUDED.jockey_id, runners.jockey_id), "
                    "draw = COALESCE(EXCLUDED.draw, runners.draw), weight_carried = COALESCE(EXCLUDED.weight_carried, runners.weight_carried), declared_weight = COALESCE(EXCLUDED.declared_weight, runners.declared_weight), finishing_pos = COALESCE(EXCLUDED.finishing_pos, runners.finishing_pos), "
                    "finishing_time = COALESCE(EXCLUDED.finishing_time, runners.finishing_time), margin = COALESCE(EXCLUDED.margin, runners.margin), running_position = COALESCE(EXCLUDED.running_position, runners.running_position), win_odds = COALESCE(EXCLUDED.win_odds, runners.win_odds)"
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
            _ingest_enrichment(conn, j)
            processed_races += 1

    print(f"Processed {processed_races} races")
    return processed_races


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _ingest_enrichment(conn, race):
    from persistence import merge_non_null
    race_id = race['race_id']
    previous = conn.execute(text('SELECT source_payload FROM races WHERE race_id=:id'), {'id':race_id}).scalar()
    race = merge_non_null(previous or {}, race)
    conn.execute(text("""UPDATE races SET venue_code=COALESCE(:venue_code,venue_code),
        race_status=COALESCE(:race_status,race_status), timezone=COALESCE(:timezone,timezone),
        scheduled_start_at_hkt=COALESCE(:scheduled,scheduled_start_at_hkt),
        actual_start_at_hkt=COALESCE(:actual,actual_start_at_hkt),
        rail_position=COALESCE(:rail,rail_position), source_payload=CAST(:payload AS JSONB)
        WHERE race_id=:id"""), dict(id=race_id, venue_code=race.get('venue_code'),
        race_status=race.get('race_status'), timezone=race.get('timezone','Asia/Hong_Kong'),
        scheduled=race.get('scheduled_start_at_hkt'),actual=race.get('actual_start_at_hkt'),
        rail=race.get('rail_position'),payload=_json(race)))
    for runner in race.get('runners',[]):
        code = runner.get('horse_code')
        horse_id=runner.get('horse_id')
        if code and horse_id:
            conn.execute(text('UPDATE horses SET hkjc_horse_code=:code WHERE horse_id=:id'),dict(code=code,id=horse_id))
            if runner.get('legacy_horse_id'):
                conn.execute(text("""INSERT INTO horse_identity_aliases(legacy_horse_id,horse_id,evidence)
                    VALUES(:legacy,:horse,CAST(:evidence AS JSONB)) ON CONFLICT DO NOTHING"""),
                    dict(legacy=runner['legacy_horse_id'],horse=horse_id,evidence=_json({'horse_code':code,'source_url':runner.get('horse_url')})))
        conn.execute(text("""UPDATE runners SET horse_no=COALESCE(:number,horse_no),horse_code=COALESCE(:code,horse_code),
            finish_status=COALESCE(:status,finish_status), source_payload=CAST(:payload AS JSONB) WHERE runner_id=:id"""),
            dict(id=runner['runner_id'],number=runner.get('horse_no'),code=code,status=runner.get('finish_status'),payload=_json(runner)))
        for section in runner.get('sectionals',[]):
            index=section['section_number']
            section_id=hashlib.sha256(f"{runner['runner_id']}|{index}".encode()).hexdigest()
            conn.execute(text("""INSERT INTO sectional_times(id,race_id,runner_id,section_index,section_time,
                source_payload,validity_status,source_url) VALUES(:id,:race,:runner,:index,:time,CAST(:payload AS JSONB),:status,:url)
                ON CONFLICT(id) DO UPDATE SET section_time=COALESCE(EXCLUDED.section_time,sectional_times.section_time),
                source_payload=EXCLUDED.source_payload, validity_status=EXCLUDED.validity_status,source_url=EXCLUDED.source_url"""),
                dict(id=section_id,race=race_id,runner=runner['runner_id'],index=index,time=section.get('section_time_seconds'),
                     payload=_json(section),status=runner.get('sectional_status'),url=race.get('sectional_source_url')))
    for dividend in race.get('dividends',[]):
        identity = [race_id, dividend.get('pool'), dividend.get('winning_combination')]
        did=hashlib.sha256(_json(identity).encode()).hexdigest()
        conn.execute(text("""INSERT INTO dividends(id,race_id,pool,winning_combination,payout_hkd,source_payload,source_url)
            VALUES(:id,:race,:pool,:combination,:amount,CAST(:payload AS JSONB),:url)
            ON CONFLICT(id) DO UPDATE SET payout_hkd=COALESCE(EXCLUDED.payout_hkd,dividends.payout_hkd),
            source_payload=EXCLUDED.source_payload,source_url=EXCLUDED.source_url"""),
            dict(id=did,race=race_id,pool=dividend.get('pool'),combination=dividend.get('winning_combination'),
                 amount=dividend.get('dividend_hkd'),payload=_json(dividend),url=race.get('source_url')))
    for key in ['weather','venue_weather']:
        observation=race.get(key)
        if not observation:
            continue
        station=observation.get('station_code') or observation.get('station')
        day=observation.get('date')
        if day != race.get('meeting_date'):
            raise ValueError('Weather date does not match race date')
        resolution=observation.get('resolution','daily')
        oid=hashlib.sha256(_json([station,day,resolution,observation.get('source_url')]).encode()).hexdigest()
        conn.execute(text("""INSERT INTO weather_observations(id,station_code,observation_date,resolution,source_payload)
            VALUES(:id,:station,:day,:resolution,CAST(:payload AS JSONB))
            ON CONFLICT(id) DO UPDATE SET source_payload=weather_observations.source_payload || EXCLUDED.source_payload"""),
            dict(id=oid,station=station,day=day,resolution=resolution,payload=_json(observation)))
        conn.execute(text("""INSERT INTO race_weather_matches(race_id,observation_id,match_policy)
            VALUES(:race,:id,'same station/date; daily summary; not pre-race') ON CONFLICT DO NOTHING"""),dict(race=race_id,id=oid))

    # Timed fields are separate observations: one-minute temperature is never labelled hourly.
    timed = race.get('race_time_weather') or {}
    target = timed.get('target_at')
    for field, observation in timed.get('fields',{}).items():
        if observation.get('status') != 'AVAILABLE' or observation.get('value') is None:
            continue
        from race_weather import aware
        end = aware(observation['observation_period_end'])
        if not target or end > aware(target) or aware(observation['known_at']) > aware(target):
            raise ValueError('Timed weather exceeds race cutoff')
        oid=hashlib.sha256(_json([observation['station_code'],field,observation['observed_at'],observation['source_url']]).encode()).hexdigest()
        conn.execute(text("""INSERT INTO weather_observations(id,station_code,observation_date,resolution,
            observed_at,period_start,period_end,known_at,field_name,source_payload)
            VALUES(:id,:station,:day,:resolution,:observed,:start,:end,:known,:field,CAST(:payload AS JSONB))
            ON CONFLICT(id) DO UPDATE SET source_payload=EXCLUDED.source_payload"""),
            dict(id=oid,station=observation['station_code'],day=end.date(),resolution=observation['resolution'],
                 observed=observation['observed_at'],start=observation['observation_period_start'],
                 end=observation['observation_period_end'],known=observation['known_at'],field=field,payload=_json(observation)))
        conn.execute(text("""INSERT INTO race_weather_matches(race_id,observation_id,match_policy,target_at,target_kind,age_minutes)
            VALUES(:race,:id,:policy,:target,:kind,:age)
            ON CONFLICT(race_id,observation_id) DO UPDATE SET target_at=EXCLUDED.target_at,
            target_kind=EXCLUDED.target_kind,age_minutes=EXCLUDED.age_minutes,match_policy=EXCLUDED.match_policy"""),
            dict(race=race_id,id=oid,policy=timed['match_policy'],target=target,
                 kind=timed['target_kind'],age=observation.get('age_minutes')))


if __name__ == '__main__':
    try:
        result = ingest()
        raise SystemExit(0 if result else 2)
    except Exception as e:
        print("Ingestion failed:", e)
        raise SystemExit(1)
