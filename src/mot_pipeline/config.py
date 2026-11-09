"""Paths, constants and thresholds shared by every pipeline stage.

Everything that depends on *where* data lives goes through `Layout`, so a future stage
(publishing, scheduled refresh) only needs a `Layout` and a year.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DATASET_ID = "c63fca52-ae4c-4b75-bab5-8b4735e1a4c9"
DATASET_PAGE = f"https://www.data.gov.uk/dataset/{DATASET_ID}/anonymised-mot-tests-and-results"
CKAN_PACKAGE_SHOW = "https://data.gov.uk/api/action/package_show"

DATA_DIR_ENV = "MOT_DATA_DIR"
DEFAULT_DATA_DIR = Path("data")
# The download manifest is committed to git (unlike data/), so it lives outside the data dir.
MANIFEST_ENV = "MOT_MANIFEST"
DEFAULT_MANIFEST = Path("manifest") / "downloads.json"
DEFAULT_YEAR = 2024

# Only the post-May-2018 regime is in scope (docs/DECISIONS.md, D-002).
MIN_SUPPORTED_YEAR = 2018

# Lookup CSV file name inside lookup.zip -> output table name.
LOOKUP_TABLES: dict[str, str] = {
    "item_detail.csv": "item_detail",
    "item_group.csv": "item_group",
    "mdr_rfr_location.csv": "rfr_locations",
    "mdr_fuel_types.csv": "fuel_types",
    "mdr_test_outcome.csv": "test_outcomes",
    "mdr_test_type.csv": "test_types",
}

# Primary key columns of each lookup table (checked for nulls and duplicates).
LOOKUP_KEYS: dict[str, tuple[str, ...]] = {
    "item_detail": ("rfr_id", "test_class_id"),
    "item_group": ("test_item_id", "test_class_id"),
    "rfr_locations": ("location_id",),
    "fuel_types": ("fuel_type_code",),
    "test_outcomes": ("test_result_code",),
    "test_types": ("test_type_code",),
}

# Validation thresholds (fractions, 0..1).
MIN_ITEM_TEST_JOIN_RATE = 0.99  # FAIL below: failure items whose test_id is in tests
MIN_FAILED_TESTS_WITH_ITEM = 0.95  # WARN below: 'F' tests that have >= 1 'F' item
MIN_ITEM_DETAIL_JOIN_RATE = 0.99  # WARN below: items whose (rfr_id, class) is in item_detail
MAX_REJECT_RATE = 0.001  # FAIL above: CSV rows DuckDB could not parse

SQL_DIR_CANDIDATES = (
    Path(__file__).parent / "sql",  # installed wheel (force-included by hatch)
    Path(__file__).resolve().parents[2] / "sql",  # source checkout / editable install
)


def sql_dir() -> Path:
    for candidate in SQL_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not find the sql/ directory")


def resolve_data_dir(override: Path | None = None) -> Path:
    """`--data-dir` wins, then $MOT_DATA_DIR, then ./data."""
    if override is not None:
        return override
    env = os.environ.get(DATA_DIR_ENV)
    return Path(env) if env else DEFAULT_DATA_DIR


def resolve_manifest(override: Path | None = None) -> Path:
    """`--manifest` wins, then $MOT_MANIFEST, then ./manifest/downloads.json."""
    if override is not None:
        return override
    env = os.environ.get(MANIFEST_ENV)
    return Path(env) if env else DEFAULT_MANIFEST


@dataclass(frozen=True)
class Layout:
    """Directory layout under the data root. Pure path arithmetic, no I/O."""

    root: Path
    manifest_path: Path | None = None  # None: keep it next to the raw files (used by tests)

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def manifest(self) -> Path:
        return self.manifest_path or self.raw / "manifest.json"

    def raw_zip(self, kind: str, year: int | None = None) -> Path:
        if kind == "lookups":
            return self.raw / "lookups" / "lookup.zip"
        return self.raw / str(year) / f"{kind}.zip"

    def extracted(self, kind: str, year: int | None = None) -> Path:
        if kind == "lookups":
            return self.root / "extracted" / "lookups"
        return self.root / "extracted" / str(year) / kind

    def output_root(self, sample: int | None) -> Path:
        # Sample runs get their own tree so they can never overwrite a full run.
        return self.root / "sample" / "parquet" if sample else self.root / "parquet"

    def table_dir(self, table: str, sample: int | None) -> Path:
        return self.output_root(sample) / table

    def lookup_file(self, table: str, sample: int | None) -> Path:
        return self.output_root(sample) / "lookups" / f"{table}.parquet"

    def work_db(self, year: int, sample: int | None) -> Path:
        suffix = "_sample" if sample else ""
        return self.root / "work" / f"{year}{suffix}.duckdb"

    def stats_file(self, year: int, sample: int | None) -> Path:
        suffix = "_sample" if sample else ""
        return self.root / "reports" / f"transform_stats_{year}{suffix}.json"

    def report_file(self, year: int, sample: int | None) -> Path:
        suffix = "_sample" if sample else ""
        return self.root / "reports" / f"validation_{year}{suffix}.json"
