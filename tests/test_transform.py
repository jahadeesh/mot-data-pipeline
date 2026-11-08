from pathlib import Path

import duckdb
import pytest

from mot_pipeline.config import Layout
from mot_pipeline.extract import extract_zip
from mot_pipeline.transform import render, run_transform, sql_literal
from tests.conftest import fixture_zips, read_json


def extract_fixtures(layout: Layout, year: int = 2024, sample: int | None = None) -> None:
    for kind, body in fixture_zips().items():
        zip_path = layout.raw_zip(kind, year)
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(body)
        extract_zip(
            zip_path, layout.extracted(kind, year), sample=None if kind == "lookups" else sample
        )


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    layout = Layout(tmp_path / "data")
    extract_fixtures(layout)
    return layout


def query(sql: str) -> list[tuple]:
    return duckdb.sql(sql).fetchall()


def parquet_tests(layout: Layout, sample: int | None = None) -> str:
    return (layout.table_dir("tests", sample) / "test_year=2024" / "*.parquet").as_posix()


def parquet_items(layout: Layout, sample: int | None = None) -> str:
    return (layout.table_dir("failure_items", sample) / "test_year=2024" / "*.parquet").as_posix()


def test_full_run_dedupes_and_cleans_tests(layout: Layout):
    stats = run_transform(layout, 2024, log=lambda _: None)
    t = parquet_tests(layout)

    assert stats["tests"]["raw_rows"] == 16
    assert stats["tests"]["rows_out"] == 13
    assert stats["tests"]["conflicting_test_ids"] == 1
    assert query(f"SELECT count(*), count(DISTINCT test_id) FROM '{t}'") == [(13, 13)]

    # Real-file CSV dialect: backslash-escaped quotes parse, repeated headers aren't "rejects".
    assert query(f"SELECT model FROM '{t}' WHERE test_id = 2003") == [('STREETZONE 50 2T 12"',)]
    assert stats["tests"]["rejected_rows"] == 0
    assert stats["tests"]["repeated_header_rows"] == 1
    assert stats["failure_items"]["repeated_header_rows"] == 1

    # Conflicting duplicate: deterministic pick (colour A-Z when everything else ties).
    assert query(f"SELECT colour FROM '{t}' WHERE test_id = 1005") == [("BLUE",)]
    # Fuel descriptions normalised to codes; unknown kept and reported.
    fuels = dict(query(f"SELECT test_id, fuel_type FROM '{t}' WHERE test_id IN (1003, 1007, 1009)"))
    assert fuels == {1003: "EL", 1007: "HY", 1009: "WOOD"}
    assert stats["tests"]["unknown_fuel_values"] == [{"value": "WOOD", "rows": 1}]
    # Mileage: float -> int, 0 and blank -> NULL.
    miles = dict(
        query(f"SELECT test_id, test_mileage FROM '{t}' WHERE test_id IN (1001, 1003, 1004)")
    )
    assert miles == {1001: 65494, 1003: None, 1004: None}


def test_output_schema_is_stable(layout: Layout):
    run_transform(layout, 2024, log=lambda _: None)
    # The schema stored in the files. (Hive-partition readers re-infer test_year from the
    # directory name, as BIGINT in DuckDB; see README "Output schema".)
    schema = query(
        f"DESCRIBE SELECT * FROM read_parquet('{parquet_tests(layout)}', hive_partitioning=false)"
    )
    assert [(c[0], c[1]) for c in schema] == [
        ("test_id", "BIGINT"),
        ("vehicle_id", "BIGINT"),
        ("test_date", "DATE"),
        ("test_class_id", "VARCHAR"),
        ("test_type", "VARCHAR"),
        ("test_result", "VARCHAR"),
        ("test_mileage", "INTEGER"),
        ("postcode_area", "VARCHAR"),
        ("make", "VARCHAR"),
        ("model", "VARCHAR"),
        ("colour", "VARCHAR"),
        ("fuel_type", "VARCHAR"),
        ("cylinder_capacity", "INTEGER"),
        ("first_use_date", "DATE"),
        ("completed_at", "TIMESTAMP"),
        ("test_year", "SMALLINT"),
    ]
    items_schema = query(
        f"DESCRIBE SELECT * FROM read_parquet('{parquet_items(layout)}', hive_partitioning=false)"
    )
    assert [(c[0], c[1]) for c in items_schema] == [
        ("test_id", "BIGINT"),
        ("rfr_id", "INTEGER"),
        ("rfr_type_code", "VARCHAR"),
        ("location_id", "INTEGER"),
        ("is_dangerous", "BOOLEAN"),
        ("completed_at", "TIMESTAMP"),
        ("test_year", "SMALLINT"),
    ]


def test_parquet_is_zstd_compressed(layout: Layout):
    run_transform(layout, 2024, log=lambda _: None)
    codecs = query(f"SELECT DISTINCT compression FROM parquet_metadata('{parquet_tests(layout)}')")
    assert codecs == [("ZSTD",)]


def test_failure_items_drop_exact_duplicates_only(layout: Layout):
    stats = run_transform(layout, 2024, log=lambda _: None)
    i = parquet_items(layout)
    assert stats["failure_items"]["raw_rows"] == 11
    assert stats["failure_items"]["rows_out"] == 10
    rows = query(f"SELECT location_id FROM '{i}' WHERE test_id = 1002 AND rfr_id = 100 ORDER BY 1")
    assert rows == [(7,), (9,)]  # duplicate loc 7 dropped, loc 9 kept
    assert query(f"SELECT test_id FROM '{i}' WHERE is_dangerous") == [(1005,)]


def test_sample_limits_tests_and_filters_items(tmp_path: Path):
    layout = Layout(tmp_path / "data")
    extract_fixtures(layout)  # all months extracted, as after a previous full run
    stats = run_transform(layout, 2024, sample=5, log=lambda _: None)

    t, i = parquet_tests(layout, 5), parquet_items(layout, 5)
    assert query(f"SELECT list(test_id ORDER BY test_id) FROM '{t}'") == [
        ([1001, 1002, 1003, 1004, 1005],)
    ]
    assert (
        query(
            f"SELECT DISTINCT test_id FROM '{i}' WHERE test_id NOT IN (SELECT test_id FROM '{t}')"
        )
        == []
    )
    assert stats["sample"] == 5
    # Sample output lives in its own tree.
    assert not layout.table_dir("tests", None).exists()
    assert layout.lookup_file("item_detail", 5).exists()


def test_lookups_handle_embedded_quotes_and_rename_columns(layout: Layout):
    stats = run_transform(layout, 2024, log=lambda _: None)
    detail = layout.lookup_file("item_detail", None).as_posix()
    assert query(f"SELECT rfr_insp_manual_desc FROM '{detail}' WHERE rfr_id = 101") == [
        ('a "leaking" brake hose',)
    ]
    assert query(f"SELECT minor_item FROM '{detail}' WHERE rfr_id = 200") == [(True,)]
    loc = layout.lookup_file("rfr_locations", None).as_posix()
    assert query(f"SELECT location_id, longitudinal FROM '{loc}' WHERE location_id = 7") == [
        (7, "Front")
    ]
    assert all(s["rejected_rows"] == 0 for s in stats["lookups"].values())


def test_rerun_overwrites_partition_and_writes_stats(layout: Layout):
    run_transform(layout, 2024, log=lambda _: None)
    run_transform(layout, 2024, log=lambda _: None)
    assert query(f"SELECT count(*) FROM '{parquet_tests(layout)}'") == [(13,)]
    assert read_json(layout.stats_file(2024, None))["tests"]["rows_out"] == 13
    assert not layout.work_db(2024, None).exists()


def test_bad_rows_are_rejected_not_fatal(layout: Layout):
    bad = layout.extracted("results", 2024) / "test_result_202402.csv"
    bad.write_bytes(
        bad.read_bytes()
        + b"not_a_number,1,2024-02-03,4,NT,P,1.0,X,A,B,C,PE,1,2020-01-01,2024-02-03T00:00:00Z\n"
    )
    stats = run_transform(layout, 2024, log=lambda _: None)
    assert stats["tests"]["rejected_rows"] == 1
    assert stats["tests"]["rows_out"] == 13


def test_render_fails_on_missing_placeholder():
    with pytest.raises(KeyError):
        render("tests.sql", input_files="[]")


def test_sql_literal_escapes_quotes():
    assert sql_literal(Path("C:/it's/data")) == "'C:/it''s/data'"
