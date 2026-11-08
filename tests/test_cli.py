"""End-to-end CLI tests against the fake server (no network, synthetic fixtures only)."""

from pathlib import Path

import duckdb
from typer.testing import CliRunner

from mot_pipeline.cli import app
from tests.conftest import URLS, FakeServer, read_json

runner = CliRunner()


def test_run_sample_end_to_end(fake_server: FakeServer, make_client_for, data_dir: Path):
    make_client_for(fake_server)
    result = runner.invoke(app, ["run", "--year", "2024", "--sample", "5"])
    assert result.exit_code == 0, result.output
    assert "Validation PASSED" in result.output

    tests = data_dir / "sample" / "parquet" / "tests" / "test_year=2024"
    assert duckdb.sql(f"SELECT count(*) FROM '{tests.as_posix()}/*.parquet'").fetchall() == [(5,)]
    assert read_json(data_dir / "reports" / "validation_2024_sample.json")["passed"] is True
    assert (data_dir / "raw" / "manifest.json").exists()


def test_rerun_skips_download(fake_server: FakeServer, make_client_for, data_dir: Path):
    make_client_for(fake_server)
    assert runner.invoke(app, ["run", "--sample", "5"]).exit_code == 0
    gets = fake_server.count("GET", URLS["results"])
    result = runner.invoke(app, ["run", "--sample", "5"])
    assert result.exit_code == 0, result.output
    assert fake_server.count("GET", URLS["results"]) == gets
    assert "skipped (up to date)" in result.output


def test_full_run_and_data_dir_option(fake_server: FakeServer, make_client_for, tmp_path: Path):
    make_client_for(fake_server)
    root = tmp_path / "elsewhere"
    result = runner.invoke(app, ["run", "--data-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert (root / "parquet" / "tests" / "test_year=2024").is_dir()
    assert (root / "parquet" / "lookups" / "item_detail.parquet").exists()


def test_validate_exits_non_zero_without_output(data_dir: Path):
    result = runner.invoke(app, ["validate", "--year", "2024"])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_rejects_pre_2019_years():
    result = runner.invoke(app, ["run", "--year", "2017"])
    assert result.exit_code == 2


def test_extract_without_download_is_a_clear_error(data_dir: Path):
    result = runner.invoke(app, ["extract"])
    assert result.exit_code == 2
    assert "download" in result.output
