-- Additive only: retain existing daily observations. New observations carry field intervals.
ALTER TABLE weather_observations DROP CONSTRAINT IF EXISTS weather_observations_resolution_check;
ALTER TABLE weather_observations ADD CONSTRAINT weather_observations_resolution_check
    CHECK (resolution IN ('daily','hourly','subhourly'));
ALTER TABLE weather_observations ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ;
ALTER TABLE weather_observations ADD COLUMN IF NOT EXISTS period_start TIMESTAMPTZ;
ALTER TABLE weather_observations ADD COLUMN IF NOT EXISTS period_end TIMESTAMPTZ;
ALTER TABLE weather_observations ADD COLUMN IF NOT EXISTS known_at TIMESTAMPTZ;
ALTER TABLE weather_observations ADD COLUMN IF NOT EXISTS field_name TEXT;
ALTER TABLE race_weather_matches ADD COLUMN IF NOT EXISTS target_at TIMESTAMPTZ;
ALTER TABLE race_weather_matches ADD COLUMN IF NOT EXISTS target_kind TEXT;
ALTER TABLE race_weather_matches ADD COLUMN IF NOT EXISTS age_minutes REAL;
