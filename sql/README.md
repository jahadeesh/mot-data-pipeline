# SQL

All transforms are DuckDB SQL. `src/mot_pipeline/transform.py` loads each file, fills
`${placeholders}` with `string.Template`, and runs it.

Placeholders are always SQL *literals* built in Python (paths are quoted and escaped there),
so the SQL files never do string concatenation themselves.

| File | Purpose |
|---|---|
| `lookups/*.sql` | One file per lookup table: read the pipe-delimited CSV, write one parquet file |
| `tests.sql` | Stage test results, dedupe to one row per `test_id`, write partitioned parquet |
| `failure_items.sql` | Stage failure items, drop exact duplicates, write partitioned parquet |
