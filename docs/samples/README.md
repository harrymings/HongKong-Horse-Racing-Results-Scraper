# Sample CSV

`hkjc_st_2026-11-14_winners_sample.csv` contains ten unmodified winner rows
selected from the fresh 2026-11-17 dashboard scrape of Sha Tin, 2026-11-14.
It retains all columns from the 140-row export, including raw sectionals,
dividends and daily/timed weather provenance. This is not the full entrant roster.
Race 6 was abandoned, so it has no fabricated CSV runner row.

Race-time weather is matched to scheduled starts. Actual off-time is unknown.
`PARTIAL` means the historical rainfall archive is unavailable; temperature,
humidity and wind are populated. No daily value is interpolated to an hourly one.

Sources: HKJC racing results, Hong Kong Observatory, HKSAR Government and
DATA.GOV.HK (weather). Weather readings are provisional. URLs, timestamps,
units and station details remain inside the CSV provenance fields. See
[the source audit](../RACE_TIME_WEATHER.md) for terms and matching policy.
