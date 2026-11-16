-- Additive migration. Back up deployed PostgreSQL and raw files before applying.
ALTER TABLE races ADD COLUMN IF NOT EXISTS venue_code TEXT;
ALTER TABLE races ADD COLUMN IF NOT EXISTS race_status TEXT;
ALTER TABLE races ADD COLUMN IF NOT EXISTS scheduled_start_at_hkt TIMESTAMPTZ;
ALTER TABLE races ADD COLUMN IF NOT EXISTS actual_start_at_hkt TIMESTAMPTZ;
ALTER TABLE races ADD COLUMN IF NOT EXISTS timezone TEXT;
ALTER TABLE races ADD COLUMN IF NOT EXISTS source_payload JSONB;
ALTER TABLE horses ADD COLUMN IF NOT EXISTS hkjc_horse_code TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_horse_code ON horses(hkjc_horse_code) WHERE hkjc_horse_code IS NOT NULL;
CREATE TABLE IF NOT EXISTS horse_identity_aliases (
    legacy_horse_id TEXT NOT NULL,
    horse_id TEXT NOT NULL REFERENCES horses(horse_id),
    evidence JSONB,
    PRIMARY KEY (legacy_horse_id, horse_id)
);
ALTER TABLE runners ADD COLUMN IF NOT EXISTS horse_no INTEGER;
ALTER TABLE runners ADD COLUMN IF NOT EXISTS horse_code TEXT;
ALTER TABLE runners ADD COLUMN IF NOT EXISTS finish_status TEXT;
ALTER TABLE runners ADD COLUMN IF NOT EXISTS source_payload JSONB;
ALTER TABLE sectional_times ADD COLUMN IF NOT EXISTS source_payload JSONB;
ALTER TABLE sectional_times ADD COLUMN IF NOT EXISTS validity_status TEXT;
ALTER TABLE sectional_times ADD COLUMN IF NOT EXISTS source_url TEXT;
-- Existing rows are never deleted to satisfy new constraints. Validation flags legacy duplicates.
CREATE TABLE IF NOT EXISTS dividends (
    id TEXT PRIMARY KEY,
    race_id TEXT NOT NULL REFERENCES races(race_id),
    pool TEXT,
    winning_combination TEXT,
    payout_hkd REAL,
    source_payload JSONB,
    source_url TEXT
);
CREATE TABLE IF NOT EXISTS weather_observations (
    id TEXT PRIMARY KEY,
    station_code TEXT,
    observation_date DATE,
    resolution TEXT CHECK (resolution IN ('daily','hourly')),
    source_payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS race_weather_matches (
    race_id TEXT NOT NULL REFERENCES races(race_id),
    observation_id TEXT NOT NULL REFERENCES weather_observations(id),
    match_policy TEXT NOT NULL,
    PRIMARY KEY (race_id, observation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_race_identity ON races(meeting_date,venue_code,race_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_runner_number ON runners(race_id,horse_no);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sectional_number ON sectional_times(runner_id,section_index);
