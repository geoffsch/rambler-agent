from datetime import timedelta
from pathlib import Path

import pytest

from rambler.config import PaceProfile, UserProfile
from rambler.geo.elevation import total_ascent_m
from rambler.geo.geometry import track_length_km
from rambler.geo.gpx import load_track
from rambler.geo.timing import estimate_duration, estimate_minutes, naismith_minutes

pytestmark = pytest.mark.unit


def test_naismith() -> None:
    assert naismith_minutes(10.0, 200.0) == pytest.approx(140.0)
    assert naismith_minutes(10.0, None) == pytest.approx(120.0)


def test_estimate_minutes_scales_and_adds_lunch() -> None:
    pace = PaceProfile(pace_factor=1.4, lunch_stop_minutes=60)
    assert estimate_minutes(10.0, 200.0, pace=pace) == pytest.approx(140 * 1.4 + 60)
    assert estimate_minutes(10.0, 200.0) == pytest.approx(140 * 1.4 + 60), "defaults match"


def test_estimate_duration_from_track(fixtures_dir: Path, example_profile_path: Path) -> None:
    oab = load_track(fixtures_dir / "gpx" / "out_and_back_ele.gpx")  # 4 km, ~100 m ascent
    adult = UserProfile.load(example_profile_path).model_copy(
        update={"pace": PaceProfile(pace_factor=1.0, lunch_stop_minutes=0)}
    )
    base = naismith_minutes(track_length_km(oab), total_ascent_m(oab))
    assert 55 <= base <= 60  # 48 min for 4 km plus ~10 min for ~100 m ascent
    assert estimate_duration(oab, adult) == timedelta(minutes=round(base))
    family = UserProfile.load(example_profile_path)  # 1.4x + 60 min lunch
    assert estimate_duration(oab, family) == timedelta(minutes=round(base * 1.4 + 60))
