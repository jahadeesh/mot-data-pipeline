"""Resumable, idempotent downloads with a manifest of sizes and checksums.

- A file whose size and ETag match the manifest and the server is skipped without re-hashing.
- Partial downloads live in `<name>.part` and resume with an HTTP Range request.
- `manifest.json` (under raw/) records url, size, sha256, etag and time for every file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from mot_pipeline.config import Layout
from mot_pipeline.sources import resolve

CHUNK_SIZE = 1 << 20  # 1 MiB
ProgressFn = Callable[[str, int, int | None], None]  # (label, bytes_advanced, total)


@dataclass
class ManifestEntry:
    url: str
    size: int
    sha256: str
    etag: str | None
    downloaded_at: str


def make_client() -> httpx.Client:
    # Long read timeout: S3 can pause mid-stream on multi-GB objects.
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=300.0),
        headers={"User-Agent": "mot-data-pipeline"},
    )


def load_manifest(path: Path) -> dict[str, ManifestEntry]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {key: ManifestEntry(**value) for key, value in raw.get("files", {}).items()}


def save_manifest(path: Path, entries: dict[str, ManifestEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"files": {k: asdict(v) for k, v in sorted(entries.items())}}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic on the same filesystem


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def remote_info(client: httpx.Client, url: str) -> tuple[int | None, str | None]:
    response = client.head(url)
    response.raise_for_status()
    length = response.headers.get("Content-Length")
    return (int(length) if length else None), response.headers.get("ETag")


def download_file(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    manifest: dict[str, ManifestEntry],
    key: str,
    verify: bool = False,
    progress: ProgressFn | None = None,
) -> tuple[ManifestEntry, bool]:
    """Download `url` to `dest`. Returns (entry, downloaded); downloaded=False means skipped."""
    size, etag = remote_info(client, url)
    known = manifest.get(key)

    if dest.exists() and known and known.size == dest.stat().st_size:
        unchanged_remotely = (size is None or size == known.size) and (
            etag is None or known.etag is None or etag == known.etag
        )
        if unchanged_remotely:
            if verify and sha256_file(dest) != known.sha256:
                raise RuntimeError(f"Checksum mismatch for {dest}; delete it and re-run")
            return known, False

    if dest.exists() and not known and size is not None and dest.stat().st_size == size:
        # File present but manifest lost: adopt it after hashing instead of re-downloading.
        entry = ManifestEntry(url, size, sha256_file(dest), etag, _now())
        return entry, False

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    # The ETag a partial was started under is kept next to it. Without it we can't prove the
    # partial bytes belong to the current remote version, so we start over.
    part_etag_file = dest.with_name(dest.name + ".part.etag")
    part_etag = part_etag_file.read_text().strip() if part_etag_file.exists() else None
    offset = part.stat().st_size if part.exists() else 0
    if offset and (part_etag is None or (size is not None and offset > size)):
        offset = 0

    headers = {}
    if offset:
        # If-Range: the server sends 206 only if the file still has the partial's ETag,
        # otherwise a full 200 response, which we write from scratch below.
        headers = {"Range": f"bytes={offset}-", "If-Range": part_etag}
    if etag:
        part_etag_file.write_text(etag)

    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        if offset and response.status_code != 206:
            offset = 0  # server ignored the range: start over
        mode = "ab" if offset else "wb"
        if progress and offset:
            progress(dest.name, offset, size)
        with part.open(mode) as fh:
            for chunk in response.iter_bytes(CHUNK_SIZE):
                fh.write(chunk)
                if progress:
                    progress(dest.name, len(chunk), size)

    actual = part.stat().st_size
    if size is not None and actual != size:
        raise RuntimeError(f"Incomplete download of {url}: {actual} of {size} bytes. Re-run.")
    part.replace(dest)
    part_etag_file.unlink(missing_ok=True)
    entry = ManifestEntry(url, actual, sha256_file(dest), etag, _now())
    return entry, True


def download_year(
    layout: Layout,
    year: int,
    client: httpx.Client,
    *,
    verify: bool = False,
    progress: ProgressFn | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Path]:
    """Download the results, failure-item and lookup ZIPs for `year`. Returns kind -> path."""
    resources = resolve(year, client)
    manifest = load_manifest(layout.manifest)
    paths: dict[str, Path] = {}
    for kind in ("lookups", "items", "results"):  # smallest first: fail fast on problems
        resource = resources[kind]
        dest = layout.raw_zip(kind, year)
        key = dest.relative_to(layout.raw).as_posix()
        entry, downloaded = download_file(
            client, resource.url, dest, manifest=manifest, key=key, verify=verify, progress=progress
        )
        manifest[key] = entry
        save_manifest(layout.manifest, manifest)  # after every file, so progress survives a crash
        status = "downloaded" if downloaded else "skipped (up to date)"
        log(f"{status}: {key} ({entry.size:,} bytes)")
        paths[kind] = dest
    return paths


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
