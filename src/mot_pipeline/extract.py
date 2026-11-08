"""Extract CSV members from the downloaded ZIPs.

DuckDB can't read inside ZIP archives without a community extension, so members are
stream-copied to disk (constant memory, `shutil.copyfileobj`). Extraction is idempotent:
a member whose extracted file already has the right size is skipped.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

MONTHLY_RE = re.compile(r"_(\d{4})(\d{2})\.csv$", re.IGNORECASE)


def csv_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Real CSV members only: skip directories and macOS `__MACOSX/` / `._*` resource forks."""
    members = []
    for info in zf.infolist():
        path = PurePosixPath(info.filename)
        if info.is_dir() or "__MACOSX" in path.parts or path.name.startswith("._"):
            continue
        if path.suffix.lower() == ".csv":
            members.append(info)
    return sorted(members, key=lambda i: PurePosixPath(i.filename).name)


def month_of(info: zipfile.ZipInfo) -> int | None:
    match = MONTHLY_RE.search(info.filename)
    return int(match.group(2)) if match else None


def select_members(members: list[zipfile.ZipInfo], sample: int | None) -> list[zipfile.ZipInfo]:
    """Sample mode keeps only the earliest month, so dev runs read ~1/12 of the data."""
    if not sample:
        return members
    monthly = [m for m in members if month_of(m) is not None]
    if not monthly:
        return members[:1]
    first = min(month_of(m) for m in monthly)
    return [m for m in monthly if month_of(m) == first]


def extract_zip(zip_path: Path, out_dir: Path, *, sample: int | None = None) -> list[Path]:
    """Extract selected CSV members of `zip_path` into `out_dir`. Returns the CSV paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in select_members(csv_members(zf), sample):
            # Use only the base name: flattens the archive's folder and prevents zip-slip.
            target = out_dir / PurePosixPath(info.filename).name
            if not (target.exists() and target.stat().st_size == info.file_size):
                tmp = target.with_name(target.name + ".part")
                with zf.open(info) as src, tmp.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1 << 20)
                tmp.replace(target)
            written.append(target)
    if not written:
        raise RuntimeError(f"No CSV members found in {zip_path}")
    return written
