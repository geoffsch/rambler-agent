import json
import random
from pathlib import Path

import httpx
import pytest

from rambler.config import Settings
from rambler.geo.elevation import backfill_elevation, moving_average, total_ascent_m
from rambler.geo.gpx import Track, TrackPoint, load_track
from rambler.http import get_client

pytestmark = pytest.mark.unit


def test_moving_average_edges_and_noop() -> None:
    assert moving_average([1, 2, 3, 4, 5], 1) == [1, 2, 3, 4, 5]
    assert moving_average([0, 0, 3, 0, 0], 3) == [0, 1, 1, 1, 0]


def test_ascent_on_clean_ramp(fixtures_dir: Path) -> None:
    oab = load_track(fixtures_dir / "gpx" / "out_and_back_ele.gpx")
    assert total_ascent_m(oab, smoothing_window=1) == pytest.approx(100.0)
    # smoothing flattens the summit a little: 5-point window on a 2.5 m/point ramp
    # loses ~5 m. Documented behaviour, not a bug.
    assert total_ascent_m(oab) == pytest.approx(100.0, rel=0.1)


def test_ascent_none_without_elevation(fixtures_dir: Path) -> None:
    assert total_ascent_m(load_track(fixtures_dir / "gpx" / "straight_1km.gpx")) is None


def test_smoothing_tames_gps_noise() -> None:
    rng = random.Random(42)
    # A genuinely flat 10 km walk with +/-3 m elevation jitter on 500 points.
    pts = [TrackPoint(0.1 + i * 0.0002, 51.3, 100.0 + rng.uniform(-3, 3)) for i in range(500)]
    t = Track(pts)
    raw = total_ascent_m(t, smoothing_window=1)
    smoothed = total_ascent_m(t, smoothing_window=5)
    assert raw is not None and smoothed is not None
    assert raw > 400, "unsmoothed noise should look like a mountain"
    assert smoothed < raw / 3


def test_backfill_uses_open_meteo_in_batches(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        lats = request.url.params["latitude"].split(",")
        return httpx.Response(200, content=json.dumps({"elevation": [7.0] * len(lats)}))

    # 150 points missing elevation, 1 already set -> 2 batches of <=100
    pts = [TrackPoint(0.1 + i * 1e-4, 51.3) for i in range(150)] + [TrackPoint(0.2, 51.3, 99.0)]
    track = Track(pts, name="t")
    client = get_client(
        Settings(data_dir=tmp_path), transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    with client:
        filled = backfill_elevation(track, client)

    assert len(calls) == 2
    assert all(r.url.host == "api.open-meteo.com" for r in calls)
    assert len(calls[0].url.params["latitude"].split(",")) == 100
    assert filled.has_elevation
    assert filled.points[0].ele == 7.0
    assert filled.points[-1].ele == 99.0, "existing elevation is left alone"
    assert filled.name == "t"
    assert not track.has_elevation, "input track is not mutated"


def test_backfill_noop_when_complete(fixtures_dir: Path) -> None:
    oab = load_track(fixtures_dir / "gpx" / "out_and_back_ele.gpx")
    assert backfill_elevation(oab, client=None) is oab  # no client needed, no network
