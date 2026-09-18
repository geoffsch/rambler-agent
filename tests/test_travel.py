import pytest

from rambler.config import Station, UserProfile
from rambler.models import Walk
from rambler.travel import estimate_access, estimates_for, rank_key, within_travel_budget

pytestmark = pytest.mark.unit

HNH = Station(name="Herne Hill", crs="HNH", termini={"VIC": 12, "BFR": 15, "CHX": None})


def walk(slug: str, termini: list[str], seeded: int | None = None) -> Walk:
    return Walk(
        source="swc",
        source_id=slug,
        slug=slug,
        title=slug,
        url="u",
        london_departure_crs=termini,
        seeded_journey_minutes=seeded,
    )


def test_termini_accept_a_bare_list_for_convenience() -> None:
    s = Station(name="X", crs="xxx", termini=["vic", "bfr"])
    assert s.crs == "XXX"
    assert s.termini == {"VIC": None, "BFR": None}
    assert s.prefers("VIC") and s.access_minutes("VIC") is None


def test_quickest_listed_terminus_wins() -> None:
    e = estimate_access(walk("w", ["BFR", "VIC"], seeded=35), HNH)
    assert e.terminus == "VIC" and e.access_minutes == 12
    assert e.onward_minutes == 35 and e.total_minutes == 47
    assert e.preferred and e.describe() == "12+35=47"


def test_listed_terminus_without_a_time_is_preferred_but_unquantified() -> None:
    e = estimate_access(walk("w", ["CHX"], seeded=40), HNH)
    assert e.preferred and e.terminus == "CHX"
    assert e.access_minutes is None and e.total_minutes is None
    assert e.describe() == "?+40"


def test_unlisted_terminus_is_flagged_not_excluded() -> None:
    e = estimate_access(walk("w", ["WAT"], seeded=50), HNH)
    assert not e.preferred and e.terminus == "WAT"
    assert e.total_minutes is None and e.describe() == "?+50"


def test_walk_with_no_termini_at_all() -> None:
    e = estimate_access(walk("w", []), HNH)
    assert e.terminus is None and not e.preferred and e.describe() == "?"


def test_ranking_prefers_listed_termini_then_speed() -> None:
    walks = {
        "quick": walk("quick", ["VIC"], seeded=30),
        "slow": walk("slow", ["VIC"], seeded=80),
        "unknown": walk("unknown", ["CHX"], seeded=30),  # listed terminus, no access time
        "unlisted": walk("unlisted", ["WAT"], seeded=10),
    }
    pairs = [(w, estimate_access(w, HNH)) for w in walks.values()]
    pairs.sort(key=lambda p: rank_key(*p))
    assert [w.slug for w, _ in pairs] == ["quick", "slow", "unknown", "unlisted"]


def test_budget_keeps_unknown_times() -> None:
    known = estimate_access(walk("a", ["VIC"], seeded=30), HNH)  # 42 total
    unknown = estimate_access(walk("b", ["WAT"], seeded=30), HNH)
    assert within_travel_budget(known, 45) and not within_travel_budget(known, 40)
    assert within_travel_budget(unknown, 10), "missing data must not hide the walk"
    assert within_travel_budget(known, None)


def test_estimates_for_rejects_an_unknown_home_station() -> None:
    profile = UserProfile(home_stations=[HNH])
    assert estimates_for([walk("a", ["VIC"])], profile, "hnh")[0][1].preferred
    with pytest.raises(ValueError, match="not a home station"):
        estimates_for([], profile, "ZZZ")


def test_crow_km_from_london() -> None:
    from rambler.travel import crow_km_from_london

    otford = walk("otford", ["VIC"]).model_copy(update={"start_lat": 51.313, "start_lon": 0.197})
    seaton = walk("seaton", ["CLJ"]).model_copy(update={"start_lat": 50.706, "start_lon": -3.073})
    assert crow_km_from_london(otford) == pytest.approx(31, abs=3)
    assert crow_km_from_london(seaton) == pytest.approx(220, abs=15)
    assert crow_km_from_london(walk("nogpx", ["VIC"])) is None


def test_unknown_times_are_ordered_by_distance_from_london() -> None:
    near = walk("kent", ["VIC"]).model_copy(update={"start_lat": 51.313, "start_lon": 0.197})
    far = walk("devon", ["VIC"]).model_copy(update={"start_lat": 50.706, "start_lon": -3.073})
    pairs = [(far, estimate_access(far, HNH)), (near, estimate_access(near, HNH))]
    pairs.sort(key=lambda p: rank_key(*p))
    assert [w.slug for w, _ in pairs] == ["kent", "devon"]

    timed = walk("timed", ["VIC"], seeded=30).model_copy(
        update={"start_lat": 50.706, "start_lon": -3.073}
    )
    pairs = [(near, estimate_access(near, HNH)), (timed, estimate_access(timed, HNH))]
    pairs.sort(key=lambda p: rank_key(*p))
    assert [w.slug for w, _ in pairs] == ["timed", "kent"], "a known time beats a guess"
