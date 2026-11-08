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


def month_of(name: str) -> int | None:
    match = MONTHLY_RE.search(name)
    return int(match.group(2)) if match else None


def select_names(names: list[str], sample: int | None) -> list[str]:
    """Sample mode keeps only the earliest month, so dev runs read ~1/12 of the data.

    Used both on ZIP member names (extract) and on extracted file names (transform), because
    the extracted folder may still hold every month from an earlier full run.
    """
    names = sorted(names, key=lambda n: PurePosixPath(n).name)
    if not sample:
        return names
    monthly = [n for n in names if month_of(n) is not None]
    if not monthly:
        return names[:1]
    first = min(month_of(n) for n in monthly)
    return [n for n in monthly if month_of(n) == first]


def select_members(members: list[zipfile.ZipInfo], sample: int | None) -> list[zipfile.ZipInfo]:
    by_name = {m.filename: m for m in members}
    return [by_name[n] for n in select_names(list(by_name), sample)]


def extracted_csvs(out_dir: Path, sample: int | None) -> list[Path]:
    """The extracted CSVs a transform should read, honouring sample mode."""
    names = [p.as_posix() for p in out_dir.glob("*.csv")]
    return [Path(n) for n in select_names(names, sample)]


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
