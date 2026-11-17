# Race-time weather source audit and implementation

Checked 2026-11-17. Source weather is provisional and attributed to Hong Kong
Observatory, the HKSAR Government and DATA.GOV.HK. Small fixtures retain source
URLs in `tests/fixtures/sources.json`. Existing daily observations are unchanged.

## Evidence and boundaries

The [HKO source catalogue](https://www.hko.gov.hk/en/abouthko/opendata_intro.htm)
provides one-minute temperature/humidity means and ten-minute wind observations.
[DATA.GOV.HK's documented archive API](https://data.gov.hk/en/help/api-spec) exposes
historical file version timestamps and downloads. Timestamped source versions,
not reconstructed daily values, are used. The live historical temperature
snapshot at archive minute 2026-11-14 12:28 contains a Sha Tin observation at
12:20 of 23.8 C. Humidity/wind snapshots at archive minute 12:27 contain 92%
humidity and northeast wind at 5 km/h, gust 10 km/h. The official declared-starters
header for that meeting gives Race 1's scheduled start as 12:30 PM HKT.

A direct historical race-card URL returned “No information” during inspection.
The HKJC declared-starters legacy `.xml.zip` URL returned HTML with scoped meeting
and race headings. The parser uses the real HTML payload, date, venue and exact
race ID; it never treats elapsed finish/split times as wall-clock starts. It does
not infer actual off-time or assume the historical schedule was known at a
prediction cutoff. A changed/unknown payload is reported unavailable.

DATA.GOV.HK archive timestamps are local GMT+8, with minute precision. The matcher
uses the end of that minute as a conservative availability upper bound and
requires both availability and observation end at/before the race target. It
keeps the source observation interval per field; a one-minute mean is labelled
subhourly, not hourly. Source rainfall represents a trailing hour, not an
instantaneous rate. The currently accessible rainfall JSON could be inspected,
but its historical archive version endpoint returned 404. No historical rainfall
values were fabricated or fetched from a nearby station.

## Sampled coverage

| Date | Temperature | Humidity | Wind | Archived rainfall |
|---|---|---|---|---|
| 2018-11-14 | No archive versions | No archive versions | No archive versions | Endpoint unavailable |
| 2022-11-14 | Sha Tin, Happy Valley | Sha Tin; Happy Valley absent | Sha Tin; Happy Valley absent | Endpoint unavailable |
| 2026-11-14 | Sha Tin, Happy Valley | Sha Tin; Happy Valley absent | Sha Tin; Happy Valley absent | Endpoint unavailable |

These are point-in-time samples of the last archived snapshot for each date.
They do not establish continuous coverage or exact station start years. The
provider re-checks each requested date and field. No citywide or nearby-station
reading is labelled racecourse weather. Dry-run reports make incomplete coverage
explicit. Live temperature/humidity/wind sources normally refresh every ten
minutes; the matched source intervals remain one or ten minutes respectively.

## Persistence, validation and operational use

- Each field retains raw text, numeric value/unit semantics, source URL and
  archive URL, station, observation start/end, archive capture and availability
  bound, interval minutes and target age.
- Missing values, missing station, missing archive, fetch failure, parse failure,
  stale/future observation and missing start time are distinct statuses.
- Matching is at/before the actual start when independently known, otherwise
  at/before the scheduled start. Default staleness is 30 minutes, configurable
  up to 120. No future-nearest or daily interpolation policy is supported.
- Date/station/target changes cannot inherit an old match. A failed refresh at
  the same target retains earlier valid fields with their original provenance
  and records the failed attempt separately.
- The archived weather's availability does not certify historical entry/schedule
  publication timing. `pre_race_verified=false`; the prediction route stays guarded.
- Migration 002 adds timestamp/field columns and permits subhourly observations.
  Individual available fields are upserted into observations and linked to each
  race target. Daily observations remain in place.
- Backfill defaults to preview. Explicit apply verifies copied JSON backups
  first, then atomically updates records and regenerates scoped existing exports.
  No production database or external historical archive was migrated in this task.

The public-data terms permit reuse subject to attribution and other conditions:
[DATA.GOV.HK terms](https://data.gov.hk/en/terms-and-conditions). This is source
attribution and an implementation record, not a legal opinion about other uses.

## Execution evidence

The full local suite passed 62 tests with isolated PostgreSQL 17. Fixtures include
original archived temperature/humidity/wind CSV snapshots, their version listings,
and small declared-starters header excerpts. Regression cases cover stale/future
data, strict publication bounds, same-target refresh preservation, changed-target
null clearing, stricter age limits, station absence, schema failures, CSV/JSON
exports and idempotent timed-field ingestion.

The generated 2026-11-14 demo JSON was backed up before enrichment, then existing
meeting CSV/JSON exports were regenerated. All ten non-abandoned races have
scheduled starts and five available timed fields (temperature, humidity, wind
speed/direction/gust). The abandoned race has no timed match. Rainfall is null.
The 50 timed field records and 50 race matches were ingested repeatedly into a
new disposable DB; full validation and timed provenance parity passed. No
production database was touched. See [machine-readable validation](race_time_weather_validation.json)
and [sampled coverage](weather_coverage_samples.json).
