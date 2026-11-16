# Data-integrity implementation and validation

Base: `main` at `5c899a55e220c286e9c993362bdb25741fddaf26`.
Working branch: `fix/racing-data-integrity`. Evidence collected 2026-11-16.

## Scope and results

This is a substantial implementation of the supplied fix plan, merged through PR #1 on 2026-11-16. The complete historical audit and several
source-dependent gates remain open. It is not a claim that every item in the plan is complete.

- **46 tests passed** with PostgreSQL 17, Python 3.14, and an isolated disposable DB.
- Compile check passed for scraper, dashboard, ingestion and API.
- Ruff fatal-error checks (`E9,F63,F7,F82`) passed; diff whitespace check passed.
- Source-derived fixtures reproduce 2026-11-14 Sha Tin Race 1 and HKO daily/climate
  responses. Other distance/class/layout/error cases are explicitly synthetic
  mutations of those fixtures, not additional official races.
- A limited live smoke test fetched only the target results and sectional pages,
  with a two-second minimum request interval. No broad historical scrape was run.
- Live results: Class 4 (Restricted), 1200 m, 14 runners. Winning horse 12,
  SECRET HEDGE (L438), 69.18 seconds, final odds 34. All 14 runners' sectional sums
  passed the 0.10-second tolerance: 42 segments total. 13 dividend records parsed.
- Daily Sha Tin weather for that date: maximum 24.8 C, minimum 21.2 C; climate mean
  unavailable and retained as null. No hourly values were inferred.
- The live record was ingested twice into a separate empty PostgreSQL DB.
  Race/runner/sectional/dividend counts and checked values matched JSON;
  odds_history remained empty. See [machine-readable report](validation_report.json).
- The pre-2019 fixture for SHA on 2018-11-14 independently resolves mean 23.4 C,
  maximum 25.7 C, minimum 22.4 C with no RYES request.

Fixture URLs and capture notes are in `tests/fixtures/sources.json`. HTML fixtures
contain small relevant source fragments; navigation, scripts and trackers were
excluded. No credentials or bulk historical dataset are committed.

## Before/after evidence

The initial checkout contained zero race JSON files, zero generated `hkjc_*`
exports and no entity mapping file. About 29 GiB of free disk was available. No
production PostgreSQL connection or historical files were supplied, so production
row counts and before/after historical affected-record totals are **not verified**.
The later local `data/` and `output/` artifacts come from the single opt-in smoke
test; they are ignored by Git. Tests use temporary directories and isolated DBs.

The source-backed regression asserts the previously broken restricted-class
heading, runner roster, winner identity and finish values. Injected race failure
reports PARTIAL (1 successful / 2 discovered) and includes the failing URL/error.
Re-scraping empty enrichment retains weather/sectionals; smaller rosters preserve
history and become blocking validation issues. Repeated database ingestion leaves
logical counts stable and does not replace known scalar values with nulls.

## Implemented safeguards

- Focused race metadata; header-mapped runner table; duplicate and malformed-row
  rejection; explicit finish statuses; raw margin/time/odds/dividend strings.
- Stable code-based horse/jockey/trainer identity, legacy horse alias evidence,
  offline horse-ID reconciliation proposals, and backups before apply.
- Exact sectional date/venue/race/horse matching; distinct failure statuses and
  configurable sum tolerance. Unknown structures fail closed.
- Atomic conservative JSON/export writes and shared daily weather enrichment;
  independent climate sources with field-level provenance and miss/error status.
- Per-race dashboard diagnostics, partial states, export manifests/validation,
  persisted job progress, overlap protection and a cancellation event.
- Additive versioned migration, uniqueness constraints, full source payloads,
  idempotent sectionals/dividend/weather ingestion, and JSON/DB validation.
- Checkpoint refresh policy, retry-failed and force-refresh CLI options; read-only
  validation CLI, inventory/backup tooling, and offline/PostgreSQL CI.
- Retrospective chronological baseline evaluation with a feature allowlist,
  train-only imputation and same-day history exclusion. `/predict` returns 409
  until independently sourced pre-race entries/cutoffs exist.

## Migration and rollout

1. Run the inventory/backup workflow in README **on the actual installation**.
   Verify a restoration into an isolated database before proceeding.
2. Validate existing files and inspect identity proposals. Resolve duplicates and
   ambiguous names on a copy. Migration will stop on duplicate constrained keys;
   it will not delete or collapse them.
3. Apply `python migrate.py --backup-verified` to the backed-up DB. For a new DB,
   first call `ensure_schema()` as documented in README.
4. Apply source-code identity reconciliation only with a new backup directory.
   Re-ingest, then run validation with `--database` and inspect value/count parity.
5. Reprocess a small verified sample before expanding to a controlled backfill.
   Keep unresolved identities and unavailable source values explicitly flagged.
6. Review the HTTP 409 prediction behavior before rollout; this intentionally
   removes an unsupported pre-race claim while retaining the route.

## Open acceptance gates and limitations

- **Historical migration/backfill/audit was not run**: existing data, output,
  mappings and production PostgreSQL were not available in this checkout.
- Expected counts are official **linked** race counts, not an independently
  reconciled race-card/declaration roster. No full meeting completeness claim.
- Unpublished and unsupported result-row spans need additional original
  fixtures. Unknown structures raise parse errors rather than being guessed or
  labeled unpublished. The `RaceNotPublished` exception remains compatible with
  callers but is not inferred from missing result rows.
- The initial audit source-validated one official HKJC race; the desktop demo now covers all 11 linked race pages for 2026-11-14, including the abandoned race. Synthetic coverage for distance,
  class, reordered/hidden cells and error scenarios is not a substitute for more
  permitted original layouts, dead heats, non-finishers and course variants.
- Scheduled/actual start timestamps remain null: no suitable race-card/off-time
  source has been integrated. The schema and exports reserve these fields.
- No historical hourly weather coverage/licensing audit was performed. Source
  availability is assessed per station/date/field response; a comprehensive
  year-by-year station coverage matrix remains to be built.
- Weather retry caches are per process. Valid and missing records are cached
  separately from transport/parse failures, but no durable weather-cache service
  was added.
- Historical no-code horse ambiguities remain unresolved. Legacy aliases are
  evidence, not a safe unconditional name-based history join. Source-code
  reconciliation is required before claiming complete longitudinal continuity.
- PostgreSQL was tested on a new disposable database, not a restored production
  copy. Legacy duplicate/constraint conflicts require a real copy audit.
- Model testing verifies chronological mechanics using synthetic multi-date data.
  No performance estimate on the user's historical dataset is claimed. Publication
  cutoffs, feature availability times and independent future entries remain open.
- Dashboard state is local-process oriented. A single-process thread lock prevents
  overlapping jobs; it is not a distributed worker queue. Remote POSTs require an
  explicit token; default operation remains localhost.

PR #1 was merged after the code checks passed. These limitations still prevent
a completed historical-data certification.

## Desktop demo follow-up

A real dashboard run on 2026-11-16 collected all 11 identified 2026-11-14 Sha Tin
race pages: 10 result rosters and one official abandonment, with 140 runner records
including withdrawals. Result/header-based discovery now includes the displayed
race even when navigation has no link to itself. Source regressions cover an
unnumbered withdrawn horse in Race 3 and abandonment in Race 6. Abandoned-race
refund dividends remain in JSON. No horse number is invented for the unnumbered
withdrawal. Sectional discrepancies retain their own unverified statuses.

Desktop screenshots use a 1440 px viewport and show the actual completed job and
preview. At 375 px, the 900 px export table scrolls inside its container instead
of collapsing columns. Last submitted course/format/grouping settings remain
visible after starting a job. See `desktop_demo_report.json` for counts and status.
