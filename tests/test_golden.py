"""Golden-set grounding checks (plan 07, tier ``golden``): need the locally ingested DB.

Run with ``uv run pytest -m golden``. Skips cleanly when ``data/walks.db`` is absent so
the unit tier and CI are unaffected.
"""

from pathlib import Path

import pytest
import yaml

from rambler.config import Settings
from rambler.db.store import WalkStore
from rambler.geo import describe
from rambler.geo.gpx import load_track

pytestmark = pytest.mark.golden

GOLDEN = Path(__file__).parent / "golden" / "walks.yaml"


def _cases() -> list[dict]:
    return yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["walks"]


@pytest.fixture(scope="module")
def store():
    db = Settings().db_path
    if not db.exists():
        pytest.skip(f"golden checks need the ingested DB at {db}; run `rambler ingest swc`")
    with WalkStore(db) as s:
        if s.count() == 0:
            pytest.skip("walks.db is empty")
        yield s


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["slug"])
def test_golden_walk_is_grounded(store: WalkStore, case: dict) -> None:
    walk = store.get_walk(case["slug"], case.get("source"))
    assert walk is not None, f"{case['slug']} missing from the DB"
    assert walk.source == case.get("source", "swc")

    # GPX exists and its geometry reproduces the published distance
    assert walk.gpx_path and walk.gpx_path.exists(), "no GPX on disk"
    stats = describe(load_track(walk.gpx_path))
    tol = case.get("computed_km_tolerance", 0.10)
    assert stats.distance_km == pytest.approx(case["published_km"], rel=tol)
    assert walk.computed_distance_km == pytest.approx(stats.distance_km, abs=0.05)

    if crs_any := case.get("london_crs_any"):
        assert set(walk.london_departure_crs) & set(crs_any), walk.london_departure_crs
    if start := case.get("start_crs"):
        assert walk.start_crs == start
    if (n := case.get("variations_min")) is not None:
        assert len(walk.variations) >= n, [v.label for v in walk.variations]
    for name in case.get("food_stop_names", []):
        assert any(name.lower() in f.name.lower() for f in walk.food_stops), [
            f.name for f in walk.food_stops
        ]
    if case.get("food_stop_with_phone"):
        assert any(f.phone for f in walk.food_stops), "no phone captured (D11)"
    if band := case.get("journey_minutes_band"):
        assert walk.seeded_journey_minutes is not None, "no seeded journey time (D10)"
        assert band[0] <= walk.seeded_journey_minutes <= band[1]
        assert walk.seeded_journey_source == "swc", "seeded times must carry provenance"


def test_seeded_journey_query_makes_no_network_call(store: WalkStore, monkeypatch) -> None:
    """D10 guard: ``max_travel_min`` must be answerable from the DB alone."""
    import httpx

    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("network call during find_walks")

    monkeypatch.setattr(httpx.Client, "send", boom)
    walks = store.find_walks(max_km=13, max_travel_min=75)
    assert walks, "expected some walks under 13 km within 75 min"
    assert all(w.seeded_journey_minutes and w.seeded_journey_minutes <= 75 for w in walks)
