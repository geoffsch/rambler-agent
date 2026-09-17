"""Ascent from elevation profiles, and elevation backfill from Open-Meteo.

Raw GPS elevation is noisy: summing every positive metre between consecutive points
over-reports ascent badly (a flat walk can "climb" hundreds of metres). We smooth
with a centred moving average first. The window is in points, not metres, which is
crude but adequate for typical 10-50 m point spacing.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

import httpx

from rambler.geo.gpx import Track, TrackPoint
from rambler.http import get_client

OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
OPEN_METEO_BATCH = 100  # documented maximum coordinates per request


def moving_average(values: Sequence[float], window: int) -> list[float]:
    """Centred moving average; edges use the available neighbours. ``window`` <= 1 is a no-op."""
    if window <= 1:
        return list(values)
    half = window // 2
    n = len(values)
    return [
        sum(values[max(0, i - half) : min(n, i + half + 1)])
        / len(values[max(0, i - half) : min(n, i + half + 1)])
        for i in range(n)
    ]


def total_ascent_m(track: Track, smoothing_window: int = 5) -> float | None:
    """Sum of positive elevation change after smoothing; ``None`` if the track lacks elevation."""
    if not track.has_elevation or len(track.points) < 2:
        return None
    eles = moving_average([p.ele for p in track.points], smoothing_window)  # type: ignore[misc]
    return sum(max(0.0, b - a) for a, b in pairwise(eles))


def fetch_elevations(
    coords: Sequence[tuple[float, float]], client: httpx.Client | None = None
) -> list[float]:
    """Elevation in metres for each ``(lon, lat)``, via Open-Meteo in batches of 100."""
    own = client is None
    client = client or get_client()
    out: list[float] = []
    try:
        for i in range(0, len(coords), OPEN_METEO_BATCH):
            batch = coords[i : i + OPEN_METEO_BATCH]
            r = client.get(
                OPEN_METEO_ELEVATION_URL,
                params={
                    "latitude": ",".join(f"{lat:.6f}" for _, lat in batch),
                    "longitude": ",".join(f"{lon:.6f}" for lon, _ in batch),
                },
            )
            r.raise_for_status()
            eles = r.json()["elevation"]
            if len(eles) != len(batch):
                raise ValueError(f"Open-Meteo returned {len(eles)} elevations for {len(batch)}")
            out.extend(float(e) for e in eles)
    finally:
        if own:
            client.close()
    return out


def backfill_elevation(track: Track, client: httpx.Client | None = None) -> Track:
    """Return a copy of the track with missing elevations filled from Open-Meteo.

    Points that already have elevation are left alone. The only network call in the
    geo package; intended for ingestion time, and cached by ``rambler.http``.
    """
    missing = [i for i, p in enumerate(track.points) if p.ele is None]
    if not missing:
        return track
    eles = fetch_elevations([(track.points[i].lon, track.points[i].lat) for i in missing], client)
    filled = list(track.points)
    for i, ele in zip(missing, eles, strict=True):
        p = filled[i]
        filled[i] = TrackPoint(p.lon, p.lat, ele)
    return Track(points=filled, name=track.name, source_path=track.source_path)
