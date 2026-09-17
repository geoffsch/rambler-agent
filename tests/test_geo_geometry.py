from pathlib import Path

import pytest

from rambler.geo.geometry import (
    bbox,
    corridor_filter,
    cumulative_distances_m,
    fraction_along,
    point_to_track_m,
    sample_along,
    slice_track,
    to_bng,
    to_wgs84,
    track_length_km,
)
from rambler.geo.gpx import Track, TrackPoint, load_track

pytestmark = pytest.mark.unit

ORIGIN_E, ORIGIN_N = 540000.0, 160000.0  # see tests/fixtures/gpx/make_fixtures.py


@pytest.fixture
def straight(fixtures_dir: Path) -> Track:
    return load_track(fixtures_dir / "gpx" / "straight_1km.gpx")


@pytest.fixture
def loop(fixtures_dir: Path) -> Track:
    return load_track(fixtures_dir / "gpx" / "loop_4km.gpx")


@pytest.fixture
def out_and_back(fixtures_dir: Path) -> Track:
    return load_track(fixtures_dir / "gpx" / "out_and_back_ele.gpx")


def bng(dx: float, dy: float) -> tuple[float, float]:
    """WGS84 (lon, lat) of a point offset from the fixture origin in BNG metres."""
    return to_wgs84(ORIGIN_E + dx, ORIGIN_N + dy)


def test_lengths_within_half_a_percent(straight: Track, loop: Track, out_and_back: Track) -> None:
    assert track_length_km(straight) == pytest.approx(1.0, rel=0.005)
    assert track_length_km(loop) == pytest.approx(4.0, rel=0.005)
    assert track_length_km(out_and_back) == pytest.approx(4.0, rel=0.005)


def test_bng_round_trip() -> None:
    lon, lat = to_wgs84(ORIGIN_E, ORIGIN_N)
    e, n = to_bng(lon, lat)
    assert (e, n) == pytest.approx((ORIGIN_E, ORIGIN_N), abs=0.01)


def test_outside_gb_raises() -> None:
    with pytest.raises(ValueError, match="outside Great Britain"):
        to_bng(51.3, 0.1)  # lat/lon swapped: the classic mistake


def test_point_to_track_and_fraction(straight: Track) -> None:
    lon, lat = bng(500.0, 100.0)  # 100 m north of the midpoint
    assert point_to_track_m(straight, lon, lat) == pytest.approx(100.0, abs=0.5)
    assert fraction_along(straight, lon, lat) == pytest.approx(0.5, abs=0.002)

    lon, lat = bng(-200.0, 0.0)  # beyond the start
    assert point_to_track_m(straight, lon, lat) == pytest.approx(200.0, abs=0.5)
    assert fraction_along(straight, lon, lat) == 0.0


def test_corridor_filter_keeps_near_points_sorted_by_position(straight: Track) -> None:
    pubs = {
        "far": bng(300.0, 400.0),  # 400 m off: excluded
        "late": bng(900.0, -50.0),  # 50 m off at 90%
        "early": bng(100.0, 200.0),  # 200 m off at 10%
    }
    hits = corridor_filter(straight, pubs.items(), max_m=250.0, key=lambda kv: kv[1])
    assert [h.item[0] for h in hits] == ["early", "late"]
    assert hits[0].distance_m == pytest.approx(200.0, abs=0.5)
    assert hits[0].fraction == pytest.approx(0.1, abs=0.002)
    assert hits[1].fraction == pytest.approx(0.9, abs=0.002)


def test_corridor_filter_default_key_is_lonlat_tuple(straight: Track) -> None:
    hits = corridor_filter(straight, [bng(500.0, 10.0)], max_m=50.0)
    assert len(hits) == 1 and hits[0].distance_m == pytest.approx(10.0, abs=0.5)


def test_bbox_is_padded_by_buffer(loop: Track) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox(loop, buffer_m=250.0)
    sw = to_bng(min_lon, min_lat)
    ne = to_bng(max_lon, max_lat)
    assert sw == pytest.approx((ORIGIN_E - 250.0, ORIGIN_N - 250.0), abs=0.5)
    assert ne == pytest.approx((ORIGIN_E + 1250.0, ORIGIN_N + 1250.0), abs=0.5)


def test_sample_along_spacing(straight: Track) -> None:
    pts = sample_along(straight, every_m=250.0)
    assert len(pts) == 5  # 0, 250, 500, 750, 1000
    es = [to_bng(*p)[0] - ORIGIN_E for p in pts]
    assert es == pytest.approx([0, 250, 500, 750, 1000], abs=0.5)


def test_cumulative_distances(straight: Track) -> None:
    cum = cumulative_distances_m(straight)
    assert cum[0] == 0.0
    assert cum[-1] == pytest.approx(1000.0, rel=0.005)
    assert cum[5] == pytest.approx(500.0, rel=0.005)


def test_slice_track_middle_half(straight: Track) -> None:
    part = slice_track(straight, 0.25, 0.75)
    assert track_length_km(part) == pytest.approx(0.5, rel=0.005)
    # interpolated ends land at 250 m and 750 m; interior vertices are the originals
    first, *_, last = part.points
    assert to_bng(first.lon, first.lat)[0] - ORIGIN_E == pytest.approx(250.0, abs=0.5)
    assert to_bng(last.lon, last.lat)[0] - ORIGIN_E == pytest.approx(750.0, abs=0.5)
    assert len(part) == 2 + 5  # 300..700 inclusive


def test_slice_track_keeps_elevation(out_and_back: Track) -> None:
    part = slice_track(out_and_back, 0.0, 0.5)  # the outbound ramp
    assert part.has_elevation
    assert part.points[0].ele == pytest.approx(50.0)
    assert part.points[-1].ele == pytest.approx(150.0, abs=0.5)


def test_slice_track_rejects_bad_fractions(straight: Track) -> None:
    with pytest.raises(ValueError):
        slice_track(straight, 0.5, 0.5)


def test_two_point_minimum() -> None:
    with pytest.raises(ValueError, match="two points"):
        track_length_km(Track([TrackPoint(0.1, 51.3)]))
