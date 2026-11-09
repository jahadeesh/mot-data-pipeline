"""Shared fixtures. Tests never touch the network or real DVSA downloads.

`fake_server` serves CKAN metadata and ZIPs built from the synthetic CSVs in tests/fixtures,
with HEAD, Range and If-Range support, through an httpx.MockTransport.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from mot_pipeline.config import CKAN_PACKAGE_SHOW

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://files.example.test/"
URLS = {
    "results": BASE + "MOT+testing+data+results+(2024).zip",
    "items": BASE + "MOT+Testing+data+failure+item+(2024).zip",
    "lookups": BASE + "lookup.zip",
}


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self, request):  # pragma: no cover - only hit by a buggy test
        raise RuntimeError(f"Tests must not use the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def _isolated_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never let a test touch the repo's committed manifest/downloads.json."""
    path = tmp_path / "manifest" / "downloads.json"
    monkeypatch.setenv("MOT_MANIFEST", str(path))
    return path


def build_data_zip(folder: str, members: dict[str, bytes]) -> bytes:
    """Mimic the real archives: a top folder, monthly CSVs, and macOS resource-fork junk."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{folder}/", b"")
        for name, data in members.items():
            zf.writestr(f"{folder}/{name}", data)
            zf.writestr(f"__MACOSX/{folder}/._{name}", b"\x00\x05\x16\x07junk")
    return buf.getvalue()


def build_lookup_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((FIXTURES / "lookup").glob("*.csv")):
            zf.writestr(path.name, path.read_bytes())
    return buf.getvalue()


def fixture_zips() -> dict[str, bytes]:
    def members(prefix: str) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(FIXTURES.glob(f"{prefix}_*.csv"))}

    return {
        "results": build_data_zip("MOT testing data results (2024)", members("test_result")),
        "items": build_data_zip("MOT Testing data failure item (2024)", members("test_item")),
        "lookups": build_lookup_zip(),
    }


def ckan_payload(urls: dict[str, str]) -> dict:
    return {
        "success": True,
        "result": {
            "resources": [
                {"name": "MOT testing data results (2023)", "url": BASE + "old_results.zip"},
                {"name": "MOT testing data results (2024)", "url": urls["results"]},
                {"name": "MOT testing data failure item (2024)", "url": urls["items"]},
                {"name": "MOT testing data lookup tables", "url": urls["lookups"]},
                {"name": "MOT testing data user guide (post-May 2018)", "url": BASE + "g.odt"},
            ]
        },
    }


class FakeServer:
    def __init__(self, files: dict[str, bytes]):
        self.files = files  # url -> body
        self.etags = {url: f'"etag-{len(body)}"' for url, body in files.items()}
        self.requests: list[httpx.Request] = []
        self.fail_after: dict[str, int] = {}  # url -> truncate the body after N bytes once

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url.copy_with(query=None))
        if url == CKAN_PACKAGE_SHOW:
            urls = {k: v for k, v in URLS.items() if v in self.files}
            return httpx.Response(200, json=ckan_payload(urls))
        if url not in self.files:
            return httpx.Response(404)
        body, etag = self.files[url], self.etags[url]
        headers = {"ETag": etag, "Accept-Ranges": "bytes"}
        if request.method == "HEAD":
            return httpx.Response(200, headers={**headers, "Content-Length": str(len(body))})
        range_header = request.headers.get("Range")
        if_range = request.headers.get("If-Range")
        if range_header and (if_range is None or if_range == etag):
            start = int(range_header.removeprefix("bytes=").split("-")[0])
            chunk = body[start:]
            headers["Content-Range"] = f"bytes {start}-{len(body) - 1}/{len(body)}"
            return httpx.Response(206, headers=headers, content=chunk)
        if url in self.fail_after:
            # Simulate a dropped connection: send only part of the body, once.
            return httpx.Response(200, headers=headers, content=body[: self.fail_after.pop(url)])
        return httpx.Response(200, headers=headers, content=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def count(self, method: str, url: str) -> int:
        return sum(1 for r in self.requests if r.method == method and str(r.url) == url)


@pytest.fixture
def fake_server() -> FakeServer:
    zips = fixture_zips()
    return FakeServer({URLS[kind]: body for kind, body in zips.items()})


@pytest.fixture
def make_client_for(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeServer], None]:
    """Route mot_pipeline.download.make_client() to a FakeServer (used by CLI tests)."""

    def install(server: FakeServer) -> None:
        monkeypatch.setattr("mot_pipeline.download.make_client", server.client)

    return install


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv("MOT_DATA_DIR", str(root))
    return root


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
