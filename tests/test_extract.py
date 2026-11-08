import io
import zipfile
from pathlib import Path

import pytest

from mot_pipeline.extract import extract_zip
from tests.conftest import build_data_zip, fixture_zips


@pytest.fixture
def results_zip(tmp_path: Path) -> Path:
    path = tmp_path / "results.zip"
    path.write_bytes(fixture_zips()["results"])
    return path


def test_extracts_all_months_and_skips_macos_junk(results_zip: Path, tmp_path: Path):
    out = tmp_path / "out"
    paths = extract_zip(results_zip, out)
    assert [p.name for p in paths] == ["test_result_202401.csv", "test_result_202402.csv"]
    assert sorted(p.name for p in out.iterdir()) == [p.name for p in paths]


def test_sample_extracts_only_first_month(results_zip: Path, tmp_path: Path):
    paths = extract_zip(results_zip, tmp_path / "out", sample=5)
    assert [p.name for p in paths] == ["test_result_202401.csv"]


def test_is_idempotent(results_zip: Path, tmp_path: Path):
    out = tmp_path / "out"
    first = extract_zip(results_zip, out)
    mtimes = {p: p.stat().st_mtime_ns for p in first}
    second = extract_zip(results_zip, out)
    assert first == second
    assert {p: p.stat().st_mtime_ns for p in second} == mtimes


def test_reextracts_truncated_file(results_zip: Path, tmp_path: Path):
    out = tmp_path / "out"
    (target,) = extract_zip(results_zip, out, sample=1)
    full = target.read_bytes()
    target.write_bytes(full[:10])
    extract_zip(results_zip, out, sample=1)
    assert target.read_bytes() == full


def test_flattens_paths_against_zip_slip(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil_202401.csv", "a\n1\n")
    path = tmp_path / "evil.zip"
    path.write_bytes(buf.getvalue())
    (written,) = extract_zip(path, tmp_path / "out")
    assert written == tmp_path / "out" / "evil_202401.csv"


def test_raises_when_no_csv(tmp_path: Path):
    path = tmp_path / "empty.zip"
    path.write_bytes(build_data_zip("folder", {}))
    with pytest.raises(RuntimeError, match="No CSV members"):
        extract_zip(path, tmp_path / "out")
