# CLAUDE.md

Conventions for working on `mot-data-pipeline`. This repo is the first of five; later repos (a text-to-SQL agent, an MCP server and an eval toolkit) query its parquet. Treat the output schema in README.md as a public contract: change it only deliberately, and update the README, `tests/test_transform.py::test_output_schema_is_stable` and explain the change in the commit message, all together.

## Stack

- Python 3.12, managed with **uv** (`uv sync`, `uv run …`). Don't use pip or venv directly.
- CLI: typer app `mot_pipeline.cli:app`, exposed as `mot-pipeline` (download | extract | transform | validate | run).
- **DuckDB for all transforms**, written as SQL files in `sql/`. Never load full files into pandas; the CSVs are several GB. Python only orchestrates.
- HTTP: httpx. Console output: rich.

## Layout

```
src/mot_pipeline/  config.py (paths, thresholds), sources.py (CKAN URL discovery), download.py,
                   extract.py, transform.py, validate.py, cli.py
sql/               lookups/*.sql, tests.sql, failure_items.sql, validate/*.sql
tests/             pytest; fixtures/ holds small SYNTHETIC CSVs only
data/              git-ignored; override with MOT_DATA_DIR or --data-dir
```

## Rules

- **Never hard-code download URLs.** Resolve them from the dataset's CKAN metadata by resource name (`sources.py`).
- **Only the post-May-2018 format** is supported (year >= 2019).
- **Downloads must stay resumable and idempotent.** Skip up-to-date files and keep `raw/manifest.json` (url, size, sha256, etag) current.
- **SQL files** use `${placeholders}` filled by `transform.render()`. Pass values as SQL literals built in Python (`sql_literal`, `sql_list`). Never concatenate SQL in the SQL files themselves. Every transform SQL file ends with a one-row stats `SELECT`.
- **Execute SQL statement by statement** with `run_sql` / `fetch_last_row` (uses `duckdb.extract_statements`). Running a multi-statement script in one `execute()` under-reported CSV rejects with DuckDB 2.0. DuckDB 2.0 relations also have no `fetchone()`; use `fetchall()`.
- Output is **parquet, zstd**, partitioned by `test_year` (the release year). Re-runs replace only that year's partition.
- **`--sample N`** must keep dev runs to seconds. It uses the first month only and writes to `data/sample/`.
- **Validation** reads the written parquet. FAIL means exit code 1. Keep thresholds in `config.py`.
- **Tests** use only `tests/fixtures` (synthetic) and never the network: `conftest.py` blocks real httpx transports. When you find a new real-data quirk, add it to the fixtures and to the table in `tests/fixtures/README.md`.
- Lint and format with **ruff** (line length 100). Pre-commit runs ruff through `uv run`, so it uses the locked version.
- **Never hard-code secrets or tokens.** Nothing in this stage needs one. Future publishing (Hugging Face) must read tokens from the environment.
- Explain non-obvious decisions in a short code comment or in README "Design decisions".
- Commit in small, logical steps with clear messages.
- Ask before downloading anything over 1 GB.

## Real-data quirks (2024 files) to remember

- The CSVs are comma-delimited with a header, ISO dates and an extra `completed_date`. The user guide (pipe-delimited, DD-MM-YYYY) is out of date. The lookup CSVs *are* pipe-delimited and contain literal `"` characters, so they're read with quoting disabled.
- Quotes inside values are **backslash-escaped** (`escape = '\'`).
- Header lines repeat mid-file. They land in DuckDB rejects and are counted separately as `repeated_header_rows`.
- About 37% of result rows are exact duplicates, and about 1.7% of test_ids have conflicting rows. About 20% of failure-item rows are exact duplicates.
- `fuel_type` mixes codes and descriptions; descriptions are normalised via `lookups/fuel_types`.
- The archives contain `__MACOSX/` junk entries, which extraction skips.

## Common commands

```bash
uv run mot-pipeline run --year 2024 --sample 100000   # end-to-end dev run (~15 s after download)
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files
```

## Out of scope so far

Hugging Face publishing, scheduled refresh workflows, years other than 2024. Keep designs open to these (everything keys off `Layout` + `year`) but don't build them unasked.
