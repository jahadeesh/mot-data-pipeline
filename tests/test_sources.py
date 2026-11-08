import pytest

from mot_pipeline.sources import SourceError, parse_resources, resolve
from tests.conftest import URLS, FakeServer


def test_resolve_picks_year_specific_resources(fake_server: FakeServer):
    with fake_server.client() as client:
        found = resolve(2024, client)
    assert found["results"].url == URLS["results"]
    assert found["items"].url == URLS["items"]
    assert found["lookups"].url == URLS["lookups"]
    assert found["results"].year == 2024


def test_name_matching_is_case_insensitive():
    resources = [
        {"name": "MOT Testing Data Results (2025)", "url": "r"},
        {"name": "MOT testing data failure items (2025)", "url": "i"},
        {"name": "MOT testing data lookup tables", "url": "l"},
    ]
    found = parse_resources(resources, 2025)
    assert {k: v.url for k, v in found.items()} == {"results": "r", "items": "i", "lookups": "l"}


def test_missing_year_raises_with_available_names():
    resources = [{"name": "MOT testing data results (2023)", "url": "x"}]
    with pytest.raises(SourceError, match=r"results.*2024"):
        parse_resources(resources, 2024)
