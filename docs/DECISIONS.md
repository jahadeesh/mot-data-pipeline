# Decisions

Every non-obvious decision in this repo: what was decided, the alternatives considered, and why.
Entries are numbered and never renumbered. A changed decision gets a new entry that supersedes
the old one.

Status: **Accepted** (in force), **Open** (still to be decided; the current behaviour is stated),
**Superseded**.

| # | Decision | Status |
|---|---|---|
| [D-001](#d-001-discover-download-urls-from-ckan-metadata) | Discover download URLs from CKAN metadata | Accepted |
| [D-002](#d-002-scope-post-may-2018-data-only) | Scope: post-May-2018 data only | Accepted (2018 handling Open) |
| [D-003](#d-003-the-user-guide-is-the-reference-but-the-real-files-win) | The user guide is the reference, but the real files win | Accepted |
| [D-004](#d-004-extract-zips-to-disk-then-let-duckdb-stream) | Extract ZIPs to disk, then let DuckDB stream | Accepted (revisit in Phase 6) |
| [D-005](#d-005-sql-files-with-template-placeholders) | SQL files with template placeholders | Accepted |
| [D-006](#d-006-stage-in-an-on-disk-duckdb-database) | Stage in an on-disk DuckDB database | Accepted |
| [D-007](#d-007-keep-exactly-one-row-per-test_id) | Keep exactly one row per `test_id` | Accepted |
| [D-008](#d-008-failure-items-drop-exact-duplicates-only) | Failure items: drop exact duplicates only | Accepted |
| [D-009](#d-009-count-malformed-csv-rows-dont-crash) | Count malformed CSV rows, don't crash | Accepted |
| [D-010](#d-010-small-value-cleaning-rules) | Small value-cleaning rules (mileage, fuel) | Accepted |
| [D-011](#d-011-test_year-is-the-release-year) | `test_year` is the release year | Accepted |
| [D-012](#d-012-sample-mode-design) | Sample mode design | Accepted |
| [D-013](#d-013-resumable-downloads-keyed-on-etag) | Resumable downloads keyed on ETag | Accepted |
| [D-014](#d-014-commit-the-download-manifest) | Commit the download manifest | Accepted |
| [D-015](#d-015-run-sql-one-statement-at-a-time) | Run SQL one statement at a time | Accepted |
| [D-016](#d-016-tests-synthetic-fixtures-and-a-fake-server) | Tests: synthetic fixtures and a fake server | Accepted |
| [D-017](#d-017-ruff-through-uv-in-pre-commit) | Ruff through uv in pre-commit | Accepted |

---

## D-001: Discover download URLs from CKAN metadata

**Decision.** `sources.py` calls the data.gov.uk CKAN API (`package_show` for the dataset ID) and
picks each resource by its *name*, e.g. `MOT testing data results (2024)`. URLs are never
hard-coded.

**Alternatives.**
- *Hard-code URLs in config.* Simple, but it breaks the next time DVSA renames a file.
- *Scrape the dataset page HTML.* This is literally "read the dataset page", but it breaks
  whenever the page layout changes.

**Why.** The file names are not stable: 2023 is `dft_test_result_2023.zip`, while 2024 is
`MOT testing data results (2024).zip`. The resource names shown on the page are stable, and the
CKAN API returns exactly the links the page displays, as structured JSON. This also makes the
Phase 6 "is there a new year?" check a single API call.

## D-002: Scope: post-May-2018 data only

**Decision.** Only data from the MOT regime introduced on 20 May 2018 is in scope.
`--year` must be 2018 or later.

**Why pre-2018 data is excluded.** On 20 May 2018, EU Directive 2014/45/EU introduced three defect
categories: Dangerous, Major and Minor. These replaced the old fail/advisory scheme, so failure
items before and after that date mean different things. The old `dangerous_mark` is explicitly not
comparable to the new Dangerous category (user guide, "Test Item Data"). Reasons for rejection
(RfRs) were also re-coded, and the file layout differs. Mixing regimes would give
downstream users, and an LLM writing SQL, numbers that look comparable but aren't.

**Alternatives.**
- *Include 2005–2017 with a `regime` flag.* That gives more history, but every defect-level
  question would need regime-aware SQL, which pushes complexity onto every consumer.
- *Start at 2019.* Phase 1 originally did this, to avoid the mixed year. It was changed to match
  the brief, which says 2018 onwards.

**Open (Phase 2).**
1. The 2018 file covers January to May under the old regime and May to December under the new
   one. Options: keep only tests on or after 2018-05-20; or keep the whole year with a regime
   column; or keep the whole year for test-level tables and only new-regime failure items.
2. The DVSA dataset page holds 2023 onwards. It says older years are on the Department for
   Transport's dataset of the same name, so discovery (D-001) will need to cover both pages.

## D-003: The user guide is the reference, but the real files win

**Decision.** The user guide (v5.1) defines meaning: codes, keys, relationships and caveats.
Where the actual files contradict it, the code follows the files, and each deviation is
documented in the SQL and in README "Data quality notes".

Deviations found in the 2024 files:

| User guide says | 2024 files actually have |
|---|---|
| Pipe-delimited | Comma-delimited (the lookup files *are* pipe-delimited) |
| Dates as DD-MM-YYYY | ISO dates (`2024-01-03`) |
| Columns `RFRTYPE`, `LOCATIONID`, `DMARK` | `rfr_type_code`, `mot_test_rfr_location_type_id`, `dangerous_mark` |
| — | An extra `completed_date` column (ISO timestamp, UTC) |
| — | Quotes inside values escaped with a backslash, not doubled |
| — | The header line repeated mid-file (15 times per month) |

**Alternative.** Follow the guide literally. That would reject every row.

**Why.** The guide was written for older extracts. Its description of what fields *mean* is still
the best source, but parsing must match the bytes on disk. These deviations were found by reading
the real file headers with HTTP range requests before writing any code, and then by the first
full sample run.

## D-004: Extract ZIPs to disk, then let DuckDB stream

**Decision.** ZIP members are stream-copied to disk with `shutil.copyfileobj`, which uses constant
memory. DuckDB then reads the CSVs directly.

**Alternatives.**
- *DuckDB's `zipfs` community extension.* No extraction is needed, but it adds a network-installed
  extension, a CI dependency and maturity risk.
- *Stream decompressed bytes from Python into DuckDB.* No disk is needed, but it's more code, and
  you lose DuckDB's parallel CSV reader.
- *pandas.* Excluded: the files are several GB.

**Why.** It is simple, debuggable (you can open the CSV DuckDB read), and DuckDB parses local files
fast and in parallel. The cost is disk: a full year is about 13 GB extracted.

**Revisit.** In Phase 6, a GitHub-hosted runner may not have that much free disk. Extracting and
transforming one month at a time, then deleting the CSVs, is the likely fix.

## D-005: SQL files with template placeholders

**Decision.** Each table has a SQL file in `sql/` with `${placeholders}`, filled by Python's
`string.Template`. Values are always built in Python as escaped SQL literals (`sql_literal`,
`sql_list`). A missing placeholder raises an error instead of rendering silently.

**Alternatives.**
- *DuckDB prepared parameters.* They can't be used for table-function file lists or `COPY … TO`
  paths, which is exactly where the variables are.
- *Jinja.* More power than needed, and logic in templates is harder to review.
- *dbt or SQLMesh.* A real option for later phases, but heavy for half a dozen models, and it
  would hide the DuckDB specifics this repo means to show.
- *SQL strings inside Python.* Harder to read and review, and the brief asks for SQL files.

**Why.** Reviewers can read the transforms as plain SQL. Each file ends with a one-row stats
`SELECT`, so the numbers in the validation report come from the same SQL that wrote the data.

## D-006: Stage in an on-disk DuckDB database

**Decision.** Transforms stage into `data/work/<year>.duckdb`, which is deleted after a successful
run, rather than into an in-memory database.

**Alternatives.**
- *In-memory.* Faster for small inputs, but a full year (about 40M rows before deduplication)
  risks running out of memory.
- *A single `COPY (SELECT … FROM read_csv(…)) TO …` with no staging.* This uses the least
  disk, but stats such as duplicates and rejects would need a second pass over the CSVs.

**Why.** An on-disk database lets DuckDB spill large sorts and window functions (the deduplication
in D-007) to disk, and gives one scan per CSV. Phase 2 will measure the cost.

## D-007: Keep exactly one row per `test_id`

**Decision.** `tests` has one row per `test_id`. Exact duplicate rows are collapsed. When a
`test_id` has *conflicting* rows, one is kept deterministically: latest `completed_date`, then
latest `first_use_date`, then `colour` A–Z. Validation reports both counts.

**Evidence.** In January 2024, 6,140,788 rows hold only 3,848,449 distinct rows (37% exact
duplicates), and 64,159 test_ids (1.7%) have conflicting rows, typically a different `colour` or
`first_use_date`.

**Alternatives.**
- *Keep all rows.* This breaks the primary key and double-counts every aggregate.
- *Drop test_ids that have conflicts.* This loses 1.7% of tests over vehicle attributes that
  rarely matter to a test outcome.
- *Put conflicts in a quarantine table.* More honest, but more surface area. A possible Phase 4
  addition.

**Why.** Consumers need `test_id` to be a real key; text-to-SQL in particular assumes it. The
conflicting fields describe the vehicle, not the test result, so the choice affects only a few
attributes, and the rule is reproducible.

## D-008: Failure items: drop exact duplicates only

**Decision.** Only whole-row duplicates are removed. The same RfR on the same test at a
*different* location is kept.

**Alternatives.**
- *One row per `(test_id, rfr_id)`.* This would merge genuinely separate defects, such as two
  different tyres with the same fault.
- *Keep everything.* This would inflate defect counts by about 20%.

**Why.** Exact duplicates carry no information. The guide allows several items per test, and a
location makes an item distinct.

## D-009: Count malformed CSV rows, don't crash

**Decision.** DuckDB reads with explicit column types and `store_rejects = true`. Rejected rows are
counted per table. Validation fails the run if more than 0.1% of rows are rejected, and warns for
any non-zero count. Repeated header lines are counted separately, because they aren't data.

**Alternatives.**
- *Fail on the first bad row (the default).* One odd line in 6 million would stop a nightly
  refresh.
- *`ignore_errors` alone.* Rows would disappear without a trace.

**Why.** Rejects stay visible and measurable without being fatal. The first real run showed why
this matters: the rejects revealed the backslash-escape issue (D-003), whose unterminated quotes
were also swallowing the rows that followed. After the fix, rejects are 0.

## D-010: Small value-cleaning rules

**Decision.**
- `test_mileage`: the float string becomes an INTEGER. 0, blank or negative becomes NULL.
- `fuel_type`: descriptions (`Electric`, `Hybrid Electric (Clean)`) are mapped to codes (`EL`,
  `HY`) via the fuel lookup. Unknown values are kept and reported.

**Alternatives.** Keep raw values, or drop unknown values.

**Why.** The guide says 0 or blank mileage means no reading was taken, so 0 is not a real
mileage, and averages would be skewed by it. Mixed fuel encodings would split every group-by in
two. Unknown values are kept rather than guessed at. Phase 3 adds readable labels while keeping
the codes.

## D-011: `test_year` is the release year

**Decision.** `test_year` (the partition key) is the year of the release file the row came from.
Validation proves that `year(test_date)` and `year(completed_at)` match it.

**Alternative.** Derive it from `year(test_date)` for tests and from the parent test for failure
items.

**Why.**
- Re-running a year replaces exactly one partition, so re-runs stay idempotent.
- Partitioning failure items needs no join of several hundred million items to tens of millions
  of tests.
- A misplaced date becomes a visible validation failure, instead of a row silently landing in
  another year's partition, where a later re-run of that year would delete it.

`test_year` is also written *inside* each file (`WRITE_PARTITION_COLUMNS`), so files stay
self-describing when copied or published individually. Hive-aware readers re-infer its type from
the directory name; DuckDB reads it as BIGINT.

## D-012: Sample mode design

**Decision.** `--sample N`:
- extracts only the first month;
- keeps the N lowest `test_id`s after deduplication;
- keeps only the failure items of those tests;
- writes to a separate `data/sample/` tree.

**Alternatives.**
- *The first N CSV rows.* Duplicates would make this fewer than N tests, and the matching failure
  items could be anywhere in the file.
- *A random sample across the year (`TABLESAMPLE`).* That still reads the whole year, so it isn't
  fast.
- *Sample failure items independently.* Most items would then have no parent test, and the join
  checks would be meaningless.

**Why.** It runs in about 15 seconds on real data, is deterministic, and is effectively random,
since test_ids carry no meaning. Joins stay complete, and a sample can never overwrite a full run.

## D-013: Resumable downloads keyed on ETag

**Decision.** A partial download is kept as `<file>.part`, with the ETag it was started under
stored in `<file>.part.etag`. A resume sends `Range` plus `If-Range: <that ETag>`. If the remote
file has changed, the server sends the whole file and the download starts over. A partial with no
recorded ETag is discarded.

**Alternative.** Resume with `Range` alone. If DVSA republished the file in the meantime, the
result is a corrupt file stitched from two versions. An early version of this code had exactly
that bug, and a test now covers it.

**Why.** The files are 0.7–1.5 GB, so resuming matters. Correctness matters more, and every
completed file is also SHA-256 checked into the manifest (D-014).

## D-014: Commit the download manifest

**Decision.** `manifest/downloads.json` is committed. For every file it records the source file
name, URL, size, SHA-256, ETag and download date. If a re-download produces identical bytes, the
existing entry is kept, so the file changes only when DVSA's data changes. It can be overridden
with `--manifest` or `MOT_MANIFEST`.

**Alternative.** Keep it next to the git-ignored data. That's simpler, but the record of which
inputs built a release disappears.

**Why.** It makes builds reproducible and auditable: anyone can check they processed byte-identical
inputs. It is also the state the Phase 6 refresh job compares against to detect new files.

## D-015: Run SQL one statement at a time

**Decision.** SQL files are split with `duckdb.extract_statements`, and each statement is run and
fully fetched in turn (`transform.fetch_last_row`).

**Alternative.** Pass the whole script to one `execute()`.

**Why.** With DuckDB 2.0, the single-call version under-reported CSV rejects. This was found by a
test that injects a bad row. An unfetched result also blocks the connection in 2.0.

## D-016: Tests: synthetic fixtures and a fake server

**Decision.**
- Tests use small hand-written CSVs in `tests/fixtures`, zipped at test time to mimic the real
  archives (top folder, monthly files, `__MACOSX` junk).
- HTTP goes through an `httpx.MockTransport` fake server that supports HEAD, Range and If-Range.
- An autouse fixture makes any real network call fail.
- Each real-data quirk, once discovered, becomes a fixture row.

**Alternatives.**
- *A slice of real data.* The brief forbids this, and it's large and opaque.
- *Recorded HTTP cassettes (vcrpy).* These can't easily simulate dropped connections or changed
  ETags.
- *A local HTTP server.* The standard-library one doesn't support Range requests.

**Why.** Tests are fast (seconds), deterministic and free to run in CI, and each fixture row
documents a specific behaviour.

## D-017: Ruff through uv in pre-commit

**Decision.** The pre-commit ruff hooks are `language: system` and run `uv run ruff`.

**Alternative.** The `ruff-pre-commit` repo pinned to a version tag.

**Why.** There is one source of truth for the ruff version (`uv.lock`), shared by local hooks
and CI, so the hook and CI can't disagree about formatting.
