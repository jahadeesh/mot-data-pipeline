"""Discover download URLs from the dataset's data.gov.uk (CKAN) metadata.

URLs are never hard-coded: DVSA renames files between releases (e.g. `dft_test_result_2023.zip`
vs `MOT testing data results (2024).zip`), but the resource *names* on the dataset page follow a
stable pattern. The CKAN API returns exactly the links shown on the dataset page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from mot_pipeline.config import CKAN_PACKAGE_SHOW, DATASET_ID

# kind -> regex on the resource name, with the year as group 1.
YEARLY_PATTERNS: dict[str, re.Pattern[str]] = {
    "results": re.compile(r"^MOT testing data results \((\d{4})\)$", re.IGNORECASE),
    "items": re.compile(r"^MOT testing data failure items? \((\d{4})\)$", re.IGNORECASE),
}
LOOKUPS_PATTERN = re.compile(r"^MOT testing data lookup tables$", re.IGNORECASE)


class SourceError(RuntimeError):
    """The dataset metadata does not contain what we need."""


@dataclass(frozen=True)
class Resource:
    kind: str  # "results" | "items" | "lookups"
    name: str
    url: str
    year: int | None = None


def fetch_resources(client: httpx.Client) -> list[dict]:
    response = client.get(CKAN_PACKAGE_SHOW, params={"id": DATASET_ID})
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise SourceError(f"CKAN package_show failed: {payload.get('error')}")
    return payload["result"]["resources"]


def parse_resources(resources: list[dict], year: int) -> dict[str, Resource]:
    """Pick the results, failure-item and lookup resources for `year`."""
    found: dict[str, Resource] = {}
    for res in resources:
        name = (res.get("name") or "").strip()
        url = res.get("url") or ""
        for kind, pattern in YEARLY_PATTERNS.items():
            match = pattern.match(name)
            if match and int(match.group(1)) == year:
                found[kind] = Resource(kind=kind, name=name, url=url, year=year)
        if LOOKUPS_PATTERN.match(name):
            found["lookups"] = Resource(kind="lookups", name=name, url=url)

    missing = {"results", "items", "lookups"} - found.keys()
    if missing:
        available = sorted((r.get("name") or "") for r in resources)
        raise SourceError(
            f"No resource for {sorted(missing)} in year {year}. Available: {available}"
        )
    return found


def resolve(year: int, client: httpx.Client) -> dict[str, Resource]:
    return parse_resources(fetch_resources(client), year)
