"""Validate the written parquet and report.

Metrics come from sql/validate/*.sql run against the parquet; thresholds live in config.py.
Statuses: FAIL (exit non-zero), WARN (printed, does not fail), PASS, INFO (context only).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from rich.console import Console
from rich.table import Table

from mot_pipeline import config
from mot_pipeline.config import Layout
from mot_pipeline.transform import run_sql, sql_literal

STATUS_STYLE = {"PASS": "green", "WARN": "yellow", "FAIL": "bold red", "INFO": "cyan"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Report:
    year: int
    sample: int | None
    checks: list[Check] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not any(c.status == "FAIL" for c in self.checks)

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    def require(self, name: str, ok: bool, detail: str, *, warn_only: bool = False) -> None:
        self.add(name, "PASS" if ok else ("WARN" if warn_only else "FAIL"), detail)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def run_validation(layout: Layout, year: int, sample: int | None = None) -> Report:
    report = Report(year=year, sample=sample)
    tests_dir = layout.table_dir("tests", sample) / f"test_year={year}"
    items_dir = layout.table_dir("failure_items", sample) / f"test_year={year}"
    for label, path in [("tests", tests_dir), ("failure_items", items_dir)]:
        if not any(path.glob("*.parquet")):
            report.add(f"{label} parquet exists", "FAIL", f"no parquet files in {path}")
    lookup_files = {t: layout.lookup_file(t, sample) for t in config.LOOKUP_KEYS}
    for table, path in lookup_files.items():
        if not path.exists():
            report.add(f"lookup {table} exists", "FAIL", f"missing {path}")
    if not report.passed:
        return report

    stats_path = layout.stats_file(year, sample)
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else None

    con = duckdb.connect()
    try:
        m = run_sql(
            con,
            "validate/metrics.sql",
            tests_glob=sql_literal(tests_dir / "*.parquet"),
            items_glob=sql_literal(items_dir / "*.parquet"),
            item_detail_file=sql_literal(lookup_files["item_detail"]),
            year=int(year),
        )
        lookups = {
            table: run_sql(
                con,
                "validate/lookup.sql",
                file=sql_literal(path),
                key=", ".join(config.LOOKUP_KEYS[table]),
                key_is_null=" OR ".join(f"{c} IS NULL" for c in config.LOOKUP_KEYS[table]),
            )
            for table, path in lookup_files.items()
        }
    finally:
        con.close()
    report.metrics = {"tables": m, "lookups": lookups}

    # Row counts
    tests_rows, items_rows = m["tests_rows"], m["items_rows"]
    if sample and stats:
        expected = min(sample, stats["tests"]["distinct_test_ids"])
        report.require(
            "tests row count", tests_rows == expected, f"{tests_rows:,} (sample of {sample:,})"
        )
    else:
        report.require("tests row count", tests_rows > 0, f"{tests_rows:,}")
    report.require("failure_items row count", items_rows > 0, f"{items_rows:,}")
    for table, lk in lookups.items():
        report.require(f"lookup {table} row count", lk["rows"] > 0, f"{lk['rows']:,}")

    # Keys
    report.require(
        "tests: test_id unique", m["tests_duplicate_ids"] == 0, f"{m['tests_duplicate_ids']:,} dup"
    )
    for table, cols in [
        ("tests", ("test_id", "test_date", "test_result", "test_class_id")),
        ("items", ("test_id", "rfr_id", "rfr_type_code")),
    ]:
        nulls = {c: m[f"{table}_null_{c}"] for c in cols}
        total = sum(nulls.values())
        detail = "none" if total == 0 else ", ".join(f"{c}={n:,}" for c, n in nulls.items() if n)
        label = "failure_items" if table == "items" else table
        report.require(f"{label}: no nulls in key columns", total == 0, detail)
    for table, lk in lookups.items():
        ok = lk["null_keys"] == 0 and lk["duplicate_keys"] == 0
        detail = f"null={lk['null_keys']}, dup={lk['duplicate_keys']}"
        report.require(f"lookup {table}: primary key valid", ok, detail)

    # Dates
    report.require(
        f"tests: test_date within {year}",
        m["tests_outside_year"] == 0,
        f"{m['tests_outside_year']:,} outside; "
        f"range {m['tests_min_date']} .. {m['tests_max_date']}",
    )
    report.require(
        f"failure_items: completed_at within {year}",
        m["items_outside_year"] == 0,
        f"{m['items_outside_year']:,} outside",
    )

    # Relationships
    join_rate = _rate(m["items_joined_to_tests"], items_rows)
    report.require(
        "failure_items join to a test",
        join_rate >= config.MIN_ITEM_TEST_JOIN_RATE,
        f"{join_rate:.2%} ({items_rows - m['items_joined_to_tests']:,} orphans; "
        f"min {config.MIN_ITEM_TEST_JOIN_RATE:.0%})",
    )
    fail_rate = _rate(m["failed_tests_with_fail_item"], m["failed_tests"])
    report.require(
        "failed tests have a fail item",
        fail_rate >= config.MIN_FAILED_TESTS_WITH_ITEM,
        f"{fail_rate:.2%} of {m['failed_tests']:,} (min {config.MIN_FAILED_TESTS_WITH_ITEM:.0%})",
        warn_only=True,
    )
    detail_rate = _rate(m["items_joined_to_item_detail"], m["items_joined_to_tests"])
    report.require(
        "failure_items join to item_detail",
        detail_rate >= config.MIN_ITEM_DETAIL_JOIN_RATE,
        f"{detail_rate:.2%} on (rfr_id, test_class_id) "
        f"(min {config.MIN_ITEM_DETAIL_JOIN_RATE:.0%})",
        warn_only=True,
    )

    # Parse rejects and cleaning summary (from transform stats)
    if stats is None:
        report.add("transform stats", "WARN", f"missing {stats_path}; reject checks skipped")
        return report
    tables = [("tests", stats["tests"]), ("failure_items", stats["failure_items"])]
    tables += [(f"lookup {t}", s) for t, s in stats["lookups"].items()]
    for label, s in tables:
        rejected = s["rejected_rows"]
        rate = _rate(rejected, s.get("raw_rows", s["rows_out"]) + rejected)
        status = "PASS" if rejected == 0 else ("FAIL" if rate > config.MAX_REJECT_RATE else "WARN")
        report.add(f"{label}: unparseable CSV rows", status, f"{rejected:,} ({rate:.3%})")

    t, i = stats["tests"], stats["failure_items"]
    report.add(
        "tests: duplicates dropped",
        "INFO",
        f"{t['raw_rows'] - t['distinct_rows']:,} exact duplicate rows; "
        f"{t['conflicting_test_ids']:,} test_ids had conflicting rows (one kept)",
    )
    report.add(
        "failure_items: duplicates dropped",
        "INFO",
        f"{i['rows_in_scope'] - i['rows_out']:,} exact duplicate rows",
    )
    unknown = t.get("unknown_fuel_values") or []
    if unknown:
        shown = ", ".join(f"{u['value']}={u['rows']:,}" for u in unknown[:5])
        report.add("tests: unrecognised fuel_type values (kept)", "INFO", shown)
    return report


def print_report(report: Report, console: Console | None = None) -> None:
    console = console or Console()
    mode = f"sample {report.sample:,}" if report.sample else "full"
    table = Table(title=f"Validation: {report.year} ({mode})", title_justify="left")
    table.add_column("Status")
    table.add_column("Check")
    table.add_column("Detail", overflow="fold")
    for check in report.checks:
        style = STATUS_STYLE[check.status]
        table.add_row(f"[{style}]{check.status}[/]", check.name, check.detail)
    console.print(table)
    verdict = "[green]PASSED[/]" if report.passed else "[bold red]FAILED[/]"
    console.print(f"Validation {verdict}")


def save_report(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "year": report.year,
        "sample": report.sample,
        "passed": report.passed,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checks": [asdict(c) for c in report.checks],
        "metrics": report.metrics,
    }
    path.write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")
