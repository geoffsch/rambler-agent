from pathlib import Path

import pytest

from rambler.db.store import WalkStore
from rambler.models import FoodStop, Provenance, Variation, VariationKind, Walk

pytestmark = pytest.mark.unit


def make_walk(slug: str, **kw) -> Walk:
    base = dict(source="swc", source_id=slug, slug=slug, title=slug.title(), url=f"u/{slug}")
    return Walk(**{**base, **kw})


@pytest.fixture
def store() -> WalkStore:
    with WalkStore() as s:
        s.upsert(
            make_walk(
                "otford",
                region="Kent",
                tags=["Darent Valley", "Popular"],
                summary="Short walk with bluebells and a pub",
                published_distance_km=12.4,
                computed_distance_km=12.40,
                computed_ascent_m=199,
                toughness=5,
                london_departure_crs=["VIC", "BFR"],
                seeded_journey_minutes=35,
                seeded_journey_source="swc",
                gpx_path=Path("data/gpx/swc/otford.gpx"),
                variations=[
                    Variation(
                        label="Train back from Shoreham",
                        kind=VariationKind.ALT_ENDING,
                        distance_km=8.0,
                    )
                ],
                food_stops=[
                    FoodStop(name="Rising Sun", phone="01958 522 683", hours_note="open Thu-Sun")
                ],
            )
        )
        s.upsert(
            make_walk(
                "long-one",
                region="Sussex",
                published_distance_km=21.0,
                toughness=7,
                london_departure_crs=["VIC"],
                seeded_journey_minutes=70,
            )
        )
        s.upsert(make_walk("bare", published_distance_km=9.0, london_departure_crs=["KGX"]))
        yield s


def test_round_trip_preserves_children_and_json_fields(store: WalkStore) -> None:
    w = store.get_walk("otford")
    assert w is not None
    assert w.tags == ["Darent Valley", "Popular"]
    assert w.london_departure_crs == ["VIC", "BFR"]
    assert w.gpx_path == Path("data/gpx/swc/otford.gpx")
    assert w.variations[0].kind == VariationKind.ALT_ENDING
    assert w.variations[0].provenance == Provenance.HEURISTIC
    assert w.food_stops[0].phone == "01958 522 683"
    assert store.get_walk("nope") is None
    assert store.count() == 3


def test_upsert_replaces_children(store: WalkStore) -> None:
    store.upsert(make_walk("otford", title="Otford v2", variations=[]))
    w = store.get_walk("otford")
    assert w.title == "Otford v2" and w.variations == [] and w.food_stops == []
    assert store.count() == 3


def test_gpx_only_source_needs_no_migration(store: WalkStore) -> None:
    """D16: a source offering just a title and a GPX must fit the schema."""
    store.upsert(
        Walk(source="trails", source_id="x1", slug="ridgeway-1", title="Ridgeway 1", url="u")
    )
    w = store.get_walk("ridgeway-1", source="trails")
    assert w and w.source == "trails" and w.variations == []
    assert store.stats()["by_source"] == {"swc": 3, "trails": 1}


def test_find_walks_filters(store: WalkStore) -> None:
    assert [w.slug for w in store.find_walks(max_km=13)] == ["bare", "otford"]
    assert [w.slug for w in store.find_walks(max_km=13, london_crs_in=["vic"])] == ["otford"]
    assert [w.slug for w in store.find_walks(max_travel_min=40)] == ["otford"]
    assert [w.slug for w in store.find_walks(max_travel_min=100)] == ["otford", "long-one"], (
        "walks without a seeded time are excluded"
    )
    assert [w.slug for w in store.find_walks(regions=["kent"])] == ["otford"]
    assert [w.slug for w in store.find_walks(max_toughness=5, min_km=10)] == ["otford"]
    assert [w.slug for w in store.find_walks(text="bluebells pub")] == ["otford"]
    assert store.find_walks(text="bluebells, (pub)") == store.find_walks(text="bluebells pub")
    assert [w.slug for w in store.find_walks(text="Darent")] == ["otford"], "tags are indexed"
    assert store.find_walks(text="zzz") == []


def test_stats(store: WalkStore) -> None:
    s = store.stats()
    assert s["total"] == 3
    assert s["with_gpx"] == 1
    assert s["with_seeded_journey"] == 2
    assert s["with_food_phone"] == 1
    assert s["with_variation_distance"] == 1
    assert s["distance_histogram_2km"] == [(8, 1), (12, 1), (20, 1)]
    assert s["distance_mismatch_over_10pct"] == []


def test_store_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "walks.db"
    with WalkStore(path) as s:
        s.upsert(make_walk("a"))
    with WalkStore(path) as s:
        assert s.count() == 1
