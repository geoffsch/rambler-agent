from datetime import timedelta
from pathlib import Path

import pytest

from rambler.geo import describe
from rambler.geo.gpx import load_track

pytestmark = pytest.mark.unit


def test_describe(fixtures_dir: Path) -> None:
    stats = describe(load_track(fixtures_dir / "gpx" / "out_and_back_ele.gpx"))
    assert stats.distance_km == pytest.approx(4.0, rel=0.005)
    assert stats.ascent_m == pytest.approx(100.0, rel=0.1)
    assert stats.has_elevation
    assert stats.point_count == 81
    assert timedelta(minutes=135) <= stats.est_duration <= timedelta(minutes=145)

    flat = describe(load_track(fixtures_dir / "gpx" / "straight_1km.gpx"))
    assert flat.ascent_m is None
    assert flat.est_duration == timedelta(minutes=round(12 * 1.4 + 60))
