import hashlib
from pathlib import Path

import pytest

from mot_pipeline.config import Layout
from mot_pipeline.download import download_file, download_year, load_manifest
from tests.conftest import URLS, FakeServer


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_download_year_writes_files_and_manifest(fake_server: FakeServer, tmp_path: Path):
    layout = Layout(tmp_path)
    with fake_server.client() as client:
        paths = download_year(layout, 2024, client, log=lambda _: None)

    assert paths["results"] == tmp_path / "raw" / "2024" / "results.zip"
    manifest = load_manifest(layout.manifest)
    assert set(manifest) == {"2024/results.zip", "2024/items.zip", "lookups/lookup.zip"}
    for kind, key in [("results", "2024/results.zip"), ("lookups", "lookups/lookup.zip")]:
        body = fake_server.files[URLS[kind]]
        assert manifest[key].size == len(body)
        assert manifest[key].sha256 == _sha(body)
        assert manifest[key].url == URLS[kind]


def test_manifest_records_source_file_name(fake_server: FakeServer, tmp_path: Path):
    layout = Layout(tmp_path)
    with fake_server.client() as client:
        download_year(layout, 2024, client, log=lambda _: None)
    manifest = load_manifest(layout.manifest)
    assert manifest["2024/items.zip"].file_name == "MOT Testing data failure item (2024).zip"
    assert manifest["lookups/lookup.zip"].file_name == "lookup.zip"


def test_redownload_of_identical_bytes_keeps_manifest_stable(
    fake_server: FakeServer, tmp_path: Path
):
    # Simulates a fresh clone: committed manifest present, data files absent.
    layout = Layout(tmp_path / "data", manifest_path=tmp_path / "manifest" / "downloads.json")
    with fake_server.client() as client:
        download_year(layout, 2024, client, log=lambda _: None)
        before = layout.manifest.read_text()
        for kind in ("results", "items", "lookups"):
            layout.raw_zip(kind, 2024).unlink()
        download_year(layout, 2024, client, log=lambda _: None)
    assert layout.manifest.read_text() == before


def test_loads_manifest_written_before_file_name_existed(tmp_path: Path):
    path = tmp_path / "m.json"
    path.write_text(
        '{"files": {"k": {"url": "https://x.test/a+b+(2024).zip", "size": 1,'
        ' "sha256": "s", "etag": null, "downloaded_at": "t"}}}'
    )
    assert load_manifest(path)["k"].file_name == "a b (2024).zip"


def test_second_run_skips_everything(fake_server: FakeServer, tmp_path: Path):
    layout = Layout(tmp_path)
    with fake_server.client() as client:
        download_year(layout, 2024, client, log=lambda _: None)
        before = fake_server.count("GET", URLS["results"])
        messages: list[str] = []
        download_year(layout, 2024, client, log=messages.append)
    assert fake_server.count("GET", URLS["results"]) == before
    assert all(m.startswith("skipped") for m in messages)


def test_resumes_partial_download_with_range(fake_server: FakeServer, tmp_path: Path):
    url = URLS["results"]
    body = fake_server.files[url]
    fake_server.fail_after[url] = len(body) // 3
    dest = tmp_path / "results.zip"
    manifest: dict = {}

    with fake_server.client() as client:
        with pytest.raises(RuntimeError, match="Incomplete download"):
            download_file(client, url, dest, manifest=manifest, key="k")
        assert (tmp_path / "results.zip.part").stat().st_size == len(body) // 3

        entry, downloaded = download_file(client, url, dest, manifest=manifest, key="k")

    assert downloaded
    assert dest.read_bytes() == body
    assert entry.sha256 == _sha(body)
    ranged = [r for r in fake_server.requests if r.headers.get("Range")]
    assert ranged and ranged[-1].headers["Range"] == f"bytes={len(body) // 3}-"


@pytest.mark.parametrize("old_etag", ['"old-etag"', None])
def test_restarts_when_partial_is_from_another_version(
    fake_server: FakeServer, tmp_path: Path, old_etag: str | None
):
    url = URLS["lookups"]
    dest = tmp_path / "lookup.zip"
    (tmp_path / "lookup.zip.part").write_bytes(b"stale bytes from an older version")
    if old_etag:
        (tmp_path / "lookup.zip.part.etag").write_text(old_etag)
    fake_server.files[url] = new = b"new content" * 10
    fake_server.etags[url] = '"new-etag"'

    with fake_server.client() as client:
        # Old ETag: If-Range mismatch gives a full 200. No ETag: we don't even try a Range.
        entry, _ = download_file(client, url, dest, manifest={}, key="k")
    assert dest.read_bytes() == new
    assert entry.etag == '"new-etag"'
    assert not (tmp_path / "lookup.zip.part.etag").exists()


def test_redownloads_when_etag_changes(fake_server: FakeServer, tmp_path: Path):
    url = URLS["lookups"]
    dest = tmp_path / "lookup.zip"
    manifest: dict = {}
    with fake_server.client() as client:
        manifest["k"], _ = download_file(client, url, dest, manifest=manifest, key="k")
        fake_server.files[url] = b"republished" + fake_server.files[url][11:]  # same size
        fake_server.etags[url] = '"republished"'
        entry, downloaded = download_file(client, url, dest, manifest=manifest, key="k")
    assert downloaded
    assert dest.read_bytes().startswith(b"republished")
    assert entry.etag == '"republished"'


def test_verify_detects_corruption(fake_server: FakeServer, tmp_path: Path):
    url = URLS["lookups"]
    dest = tmp_path / "lookup.zip"
    manifest: dict = {}
    with fake_server.client() as client:
        manifest["k"], _ = download_file(client, url, dest, manifest=manifest, key="k")
        data = bytearray(dest.read_bytes())
        data[0] ^= 0xFF
        dest.write_bytes(bytes(data))
        with pytest.raises(RuntimeError, match="Checksum mismatch"):
            download_file(client, url, dest, manifest=manifest, key="k", verify=True)


def test_adopts_existing_file_when_manifest_missing(fake_server: FakeServer, tmp_path: Path):
    url = URLS["lookups"]
    dest = tmp_path / "lookup.zip"
    dest.write_bytes(fake_server.files[url])
    with fake_server.client() as client:
        entry, downloaded = download_file(client, url, dest, manifest={}, key="k")
    assert not downloaded
    assert entry.sha256 == _sha(fake_server.files[url])
    assert fake_server.count("GET", url) == 0
