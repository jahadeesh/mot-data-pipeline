"""Run the SQL in sql/ with DuckDB to turn extracted CSVs into parquet.

Each SQL file stages its CSVs, builds a final table, COPYs it to parquet and ends with a
SELECT returning stats, which are saved to reports/transform_stats_<year>.json for validation.

Staging uses an on-disk DuckDB database under data/work/ rather than memory, so full-year runs
(~13 GB of CSV) can spill to disk instead of exhausting RAM. It is deleted after a successful run.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from string import Template

import duckdb

from mot_pipeline.config import LOOKUP_TABLES, Layout, sql_dir
from mot_pipeline.extract import extracted_csvs


def sql_literal(value: str | Path) -> str:
    """Quote a value as a SQL string literal. Paths use forward slashes (DuckDB-friendly)."""
    text = value.as_posix() if isinstance(value, Path) else str(value)
    return "'" + text.replace("'", "''") + "'"


def sql_list(paths: Iterable[Path]) -> str:
    return "[" + ", ".join(sql_literal(p) for p in paths) + "]"


def render(name: str, **params: object) -> str:
    """Fill ${placeholders} in sql/<name>. Missing placeholders raise KeyError (fail loudly)."""
    template = Template((sql_dir() / name).read_text(encoding="utf-8"))
    return template.substitute({k: str(v) for k, v in params.items()})


def fetch_last_row(con: duckdb.DuckDBPyConnection, sql: str) -> dict:
    """Run every statement in `sql`; return the last statement's single row as a dict.

    Statements run one at a time, each fully fetched. Passing the whole script to a single
    execute() under-reported CSV rejects with DuckDB 2.0, and an unfetched result blocks the
    connection.
    """
    columns: list[str] = []
    rows: list[tuple] = []
    for statement in duckdb.extract_statements(sql):
        con.execute(statement)
        columns = [d[0] for d in con.description] if con.description else []
        rows = con.fetchall()
    if len(rows) != 1:
        raise RuntimeError("SQL must end with a SELECT returning exactly one row")
    return dict(zip(columns, rows[0], strict=True))


def run_sql(con: duckdb.DuckDBPyConnection, name: str, **params: object) -> dict:
    """Render sql/<name> and run it; the file must end with a one-row stats SELECT."""
    return fetch_last_row(con, render(name, **params))


def _clear_partition(table_dir: Path, year: int) -> None:
    # Overwrite just this year's partition; other years in the same table are untouched.
    partition = table_dir / f"test_year={year}"
    if partition.exists():
        shutil.rmtree(partition)
    table_dir.mkdir(parents=True, exist_ok=True)


def run_transform(
    layout: Layout,
    year: int,
    sample: int | None = None,
    *,
    log: Callable[[str], None] = print,
) -> dict:
    lookup_dir = layout.extracted("lookups")
    result_csvs = extracted_csvs(layout.extracted("results", year), sample)
    item_csvs = extracted_csvs(layout.extracted("items", year), sample)
    if not result_csvs or not item_csvs:
        raise FileNotFoundError(f"No extracted CSVs for {year}; run `mot-pipeline extract` first")

    work_db = layout.work_db(year, sample)
    work_db.parent.mkdir(parents=True, exist_ok=True)
    work_db.unlink(missing_ok=True)
    stats: dict = {"year": year, "sample": sample, "lookups": {}}

    con = duckdb.connect(str(work_db))
    try:
        con.execute("SET preserve_insertion_order = false")  # lets DuckDB stream and spill
        con.execute(f"SET temp_directory = {sql_literal(work_db.parent / 'tmp')}")

        for csv_name, table in LOOKUP_TABLES.items():
            out = layout.lookup_file(table, sample)
            out.parent.mkdir(parents=True, exist_ok=True)
            stats["lookups"][table] = run_sql(
                con,
                f"lookups/{table}.sql",
                input_file=sql_literal(lookup_dir / csv_name),
                output_file=sql_literal(out),
            )
            log(f"lookup {table}: {stats['lookups'][table]['rows_out']:,} rows")

        tests_dir = layout.table_dir("tests", sample)
        _clear_partition(tests_dir, year)
        log(f"tests: reading {len(result_csvs)} file(s)")
        stats["tests"] = run_sql(
            con,
            "tests.sql",
            input_files=sql_list(result_csvs),
            output_dir=sql_literal(tests_dir),
            year=int(year),
            # Sample: the N lowest test_ids. Deterministic, and test_ids carry no meaning,
            # so it's effectively a random sample of the month.
            limit_clause=f"ORDER BY r.test_id LIMIT {int(sample)}" if sample else "",
        )
        log(f"tests: {stats['tests']['rows_out']:,} rows written")

        items_dir = layout.table_dir("failure_items", sample)
        _clear_partition(items_dir, year)
        log(f"failure_items: reading {len(item_csvs)} file(s)")
        stats["failure_items"] = run_sql(
            con,
            "failure_items.sql",
            input_files=sql_list(item_csvs),
            output_dir=sql_literal(items_dir),
            year=int(year),
            # Sample: keep only items of sampled tests, so joins stay meaningful.
            items_filter=("WHERE test_id IN (SELECT test_id FROM tests_final)" if sample else ""),
        )
        log(f"failure_items: {stats['failure_items']['rows_out']:,} rows written")
    finally:
        con.close()

    work_db.unlink(missing_ok=True)
    shutil.rmtree(work_db.parent / "tmp", ignore_errors=True)

    stats_path = layout.stats_file(year, sample)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, indent=2, default=str) + "\n", encoding="utf-8")
    return stats
