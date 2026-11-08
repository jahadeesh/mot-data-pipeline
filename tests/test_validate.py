import json
from pathlib import Path

import duckdb
import pytest

from mot_pipeline.config import Layout
from mot_pipeline.transform import run_transform
from mot_pipeline.validate import run_validation, save_report
from tests.test_transform import extract_fixtures


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    layout = Layout(tmp_path / "data")
    extract_fixtures(layout)
    return layout


def statuses(report) -> dict[str, str]:
    return {c.name: c.status for c in report.checks}


def rewrite_parquet(path_dir: Path, select_sql: str) -> None:
    """Replace a partition's parquet with the result of `select_sql` over its current content."""
    files = sorted(path_dir.glob("*.parquet"))
    src = [f.as_posix() for f in files]
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS {select_sql.format(src=src)}")
    for f in files:
        f.unlink()
    con.execute(f"COPY t TO '{(path_dir / 'part_0.parquet').as_posix()}' (FORMAT parquet)")
    con.close()


@pytest.mark.parametrize("sample", [None, 5])
def test_fixture_output_passes(layout: Layout, sample: int | None):
    run_transform(layout, 2024, sample, log=lambda _: None)
    report = run_validation(layout, 2024, sample)
    assert report.passed, [c for c in report.checks if c.status == "FAIL"]
    s = statuses(report)
    assert s["failure_items join to a test"] == "PASS"
    assert s["failed tests have a fail item"] == "PASS"
    assert s["tests: duplicates dropped"] == "INFO"


def test_report_details_cleaning(layout: Layout):
    run_transform(layout, 2024, log=lambda _: None)
    report = run_validation(layout, 2024)
    details = {c.name: c.detail for c in report.checks}
    assert details["tests: duplicates dropped"].startswith("2 exact duplicate rows; 1 test_ids")
    assert details["failure_items: duplicates dropped"] == "1 exact duplicate rows"
    assert details["tests: unrecognised fuel_type values (kept)"] == "WOOD=1"


def test_orphan_items_fail(layout: Layout):
    run_transform(layout, 2024, log=lambda _: None)
    tests_dir = layout.table_dir("tests", None) / "test_year=2024"
    # Drop a test that has failure items: its items become orphans (2 of 10 = 80% join).
    rewrite_parquet(tests_dir, "SELECT * FROM read_parquet({src}) WHERE test_id <> 1002")
    report = run_validation(layout, 2024)
    assert not report.passed
    assert statuses(report)["failure_items join to a test"] == "FAIL"


def test_dates_outside_year_fail(layout: Layout):
    run_transform(layout, 2024, log=lambda _: None)
    tests_dir = layout.table_dir("tests", None) / "test_year=2024"
    rewrite_parquet(
        tests_dir,
        "SELECT * REPLACE (CASE WHEN test_id = 1001 THEN DATE '2023-12-31' ELSE test_date END"
        " AS test_date) FROM read_parquet({src})",
    )
    report = run_validation(layout, 2024)
    assert statuses(report)["tests: test_date within 2024"] == "FAIL"


def test_too_many_rejects_fail(layout: Layout):
    run_transform(layout, 2024, log=lambda _: None)
    stats_path = layout.stats_file(2024, None)
    stats = json.loads(stats_path.read_text())
    stats["tests"]["rejected_rows"] = 5
    stats_path.write_text(json.dumps(stats))
    report = run_validation(layout, 2024)
    assert statuses(report)["tests: unparseable CSV rows"] == "FAIL"


def test_missing_output_fails_fast(tmp_path: Path):
    report = run_validation(Layout(tmp_path), 2024)
    assert not report.passed
    assert statuses(report)["tests parquet exists"] == "FAIL"


def test_saved_report_is_json(layout: Layout, tmp_path: Path):
    run_transform(layout, 2024, log=lambda _: None)
    report = run_validation(layout, 2024)
    out = tmp_path / "report.json"
    save_report(report, out)
    body = json.loads(out.read_text())
    assert body["passed"] is True
    assert body["metrics"]["tables"]["tests_rows"] == 12
