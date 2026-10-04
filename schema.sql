-- Relational schema for HK horse racing dataset
-- Designed for PostgreSQL / DuckDB compatibility

-- Core entities
CREATE TABLE IF NOT EXISTS horses (
    horse_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    country         TEXT,
    sex             TEXT,
    dob             DATE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trainers (
    trainer_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    country         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jockeys (
    jockey_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    country         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS races (
    race_id         TEXT PRIMARY KEY,
    meeting_date    DATE NOT NULL,
    meeting_name    TEXT,
    venue           TEXT,
    race_number     INTEGER,
    distance_m      INTEGER,
    grade           TEXT,
    going           TEXT,
    rail_position   TEXT,
    race_time       TIME,
    source_url      TEXT,
    checksum        TEXT,
    scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runners (
    runner_id       TEXT PRIMARY KEY,
    race_id         TEXT REFERENCES races(race_id) ON DELETE CASCADE,
    horse_id        TEXT REFERENCES horses(horse_id),
    trainer_id      TEXT REFERENCES trainers(trainer_id),
    jockey_id       TEXT REFERENCES jockeys(jockey_id),
    draw            INTEGER,
    weight_carried  REAL,
    declared_weight REAL,
    finishing_pos   INTEGER,
    finishing_time  REAL,
    margin          REAL,
    running_position TEXT,
    win_odds        REAL,
    official_rating INTEGER,
    equipment       TEXT,
    vet_comment     TEXT,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE runners ADD COLUMN IF NOT EXISTS declared_weight REAL;
ALTER TABLE runners ADD COLUMN IF NOT EXISTS running_position TEXT;
ALTER TABLE runners ADD COLUMN IF NOT EXISTS win_odds REAL;

-- Odds history snapshots (multiple per runner per race)
CREATE TABLE IF NOT EXISTS odds_history (
    odds_id         TEXT PRIMARY KEY,
    race_id         TEXT REFERENCES races(race_id) ON DELETE CASCADE,
    runner_id       TEXT REFERENCES runners(runner_id),
    snapshot_time   TIMESTAMP NOT NULL,
    bookmaker       TEXT,
    back_odds       REAL,
    lay_odds        REAL,
    pool_size       REAL,
    source          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sectional times (multiple sections per runner)
CREATE TABLE IF NOT EXISTS sectional_times (
    id              TEXT PRIMARY KEY,
    race_id         TEXT REFERENCES races(race_id) ON DELETE CASCADE,
    runner_id       TEXT REFERENCES runners(runner_id),
    section_index   INTEGER,
    section_time    REAL,
    split_time      REAL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weather (
    id              TEXT PRIMARY KEY,
    race_id         TEXT REFERENCES races(race_id) ON DELETE CASCADE,
    temperature_c   REAL,
    humidity        REAL,
    wind_speed_kph  REAL,
    conditions      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS track_bias (
    id              TEXT PRIMARY KEY,
    venue           TEXT,
    reference_date  DATE,
    bias_details    JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Entity mapping tables to canonicalize names and provide stable IDs
CREATE TABLE IF NOT EXISTS entity_mappings (
    canonical_id    TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,
    canonical_name  TEXT NOT NULL,
    aliases         JSONB,
    last_seen       TIMESTAMP,
    metadata        JSONB
);

-- Basic indexes for performance
CREATE INDEX IF NOT EXISTS idx_races_meeting_date ON races (meeting_date);
CREATE INDEX IF NOT EXISTS idx_runners_race_id ON runners (race_id);
CREATE INDEX IF NOT EXISTS idx_odds_history_race_time ON odds_history (snapshot_time);

-- Notes:
-- - Use deterministic stable ID generation (e.g. hash of name+dob/source) when ingesting
-- - Enforce deduplication at ingestion via checksum + source_url
-- - Consider partitioning large tables by year for performance
