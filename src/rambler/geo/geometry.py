"""Track geometry in honest metres (EPSG:27700, British National Grid).

Every function here takes lon/lat (WGS84) in and projects to BNG before doing any
maths, so distances and buffers are metres, not degrees. England-only by design:
coordinates outside a generous GB box raise ``ValueError`` rather than silently
producing garbage.

Caveat on ``fraction_along`` (and everything built on it): position is found by
nearest-point projection onto the line. On out-and-back or self-crossing tracks a
point beside the path is equally near two places, and shapely picks one. Acceptable
for v1; plan 08 can add direction-aware matching if it matters.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import TypeVar

from pyproj import Transformer
from shapely.geometry import LineString, Point

from rambler.geo.gpx import Track, TrackPoint

_TO_BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
_TO_WGS84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

#: (min_lon, min_lat, max_lon, max_lat). Generous: covers all of Great Britain.
GB_BOUNDS = (-9.0, 49.5, 2.5, 61.5)

T = TypeVar("T")
LonLat = tuple[float, float]


def _check_gb(lon: float, lat: float) -> None:
    min_lon, min_lat, max_lon, max_lat = GB_BOUNDS
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        raise ValueError(f"coordinate ({lon}, {lat}) is outside Great Britain; wrong order?")


def to_bng(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 lon/lat -> BNG easting/northing in metres."""
    _check_gb(lon, lat)
    return _TO_BNG.transform(lon, lat)


def to_wgs84(easting: float, northing: float) -> LonLat:
    """BNG easting/northing -> WGS84 (lon, lat)."""
    return _TO_WGS84.transform(easting, northing)


def track_line(track: Track) -> LineString:
    """The track as a shapely ``LineString`` in BNG metres."""
    if len(track.points) < 2:
        raise ValueError("a track needs at least two points")
    return LineString([to_bng(p.lon, p.lat) for p in track.points])


def track_length_km(track: Track) -> float:
    return track_line(track).length / 1000.0


def point_to_track_m(track: Track, lon: float, lat: float) -> float:
    """Shortest distance in metres from a point to the track."""
    return track_line(track).distance(Point(to_bng(lon, lat)))


def fraction_along(track: Track, lon: float, lat: float) -> float:
    """Where along the track (0 = start, 1 = end) the nearest point to ``lon, lat`` lies."""
    return track_line(track).project(Point(to_bng(lon, lat)), normalized=True)


@dataclass(frozen=True, slots=True)
class CorridorHit[T]:
    item: T
    distance_m: float
    fraction: float


def corridor_filter[T](
    track: Track,
    items: Iterable[T],
    *,
    max_m: float = 250.0,
    key: Callable[[T], LonLat] = lambda item: item,  # type: ignore[assignment,return-value]
) -> list[CorridorHit[T]]:
    """Keep the items within ``max_m`` metres of the track, with distance and position.

    ``key`` extracts ``(lon, lat)`` from each item; by default items are such tuples.
    Results are sorted by position along the track.
    """
    line = track_line(track)
    hits: list[CorridorHit[T]] = []
    for item in items:
        lon, lat = key(item)
        pt = Point(to_bng(lon, lat))
        d = line.distance(pt)
        if d <= max_m:
            hits.append(CorridorHit(item, d, line.project(pt, normalized=True)))
    hits.sort(key=lambda h: h.fraction)
    return hits


def bbox(track: Track, buffer_m: float = 0.0) -> tuple[float, float, float, float]:
    """Bounding box ``(min_lon, min_lat, max_lon, max_lat)`` padded by ``buffer_m`` metres."""
    min_x, min_y, max_x, max_y = track_line(track).bounds
    min_lon, min_lat = to_wgs84(min_x - buffer_m, min_y - buffer_m)
    max_lon, max_lat = to_wgs84(max_x + buffer_m, max_y + buffer_m)
    return (min_lon, min_lat, max_lon, max_lat)


def sample_along(track: Track, every_m: float = 500.0) -> list[LonLat]:
    """Points every ``every_m`` metres along the track, plus the end point.

    The end is skipped when it would sit within 5% of ``every_m`` of the last regular
    sample (so a 1000.02 m track sampled every 250 m gives five points, not six).
    """
    line = track_line(track)
    steps = int(line.length // every_m)
    distances = [i * every_m for i in range(steps + 1)]
    if line.length - distances[-1] > 0.05 * every_m:
        distances.append(line.length)
    return [to_wgs84(p.x, p.y) for p in (line.interpolate(d) for d in distances)]


def cumulative_distances_m(track: Track) -> list[float]:
    """Distance from the start to each track point, in metres (first element 0)."""
    coords = [to_bng(p.lon, p.lat) for p in track.points]
    out = [0.0]
    for (x0, y0), (x1, y1) in pairwise(coords):
        out.append(out[-1] + math.hypot(x1 - x0, y1 - y0))
    return out


def _interpolate(a: TrackPoint, b: TrackPoint, t: float) -> TrackPoint:
    ele = None if a.ele is None or b.ele is None else a.ele + (b.ele - a.ele) * t
    return TrackPoint(a.lon + (b.lon - a.lon) * t, a.lat + (b.lat - a.lat) * t, ele)


def slice_track(track: Track, start_fraction: float, end_fraction: float) -> Track:
    """The part of the track between two fractions of its length, elevation preserved.

    End points are linearly interpolated (in lon/lat, which is fine at sub-segment
    scale); interior vertices are the original points.
    """
    if not 0.0 <= start_fraction < end_fraction <= 1.0:
        raise ValueError("need 0 <= start_fraction < end_fraction <= 1")
    cum = cumulative_distances_m(track)
    total = cum[-1]
    start_d, end_d = start_fraction * total, end_fraction * total
    pts = track.points

    def at(d: float) -> TrackPoint:
        for i in range(1, len(cum)):
            if cum[i] >= d:
                seg = cum[i] - cum[i - 1]
                t = 0.0 if seg == 0 else (d - cum[i - 1]) / seg
                return _interpolate(pts[i - 1], pts[i], t)
        return pts[-1]

    interior = [p for p, d in zip(pts, cum, strict=True) if start_d < d < end_d]
    return Track(points=[at(start_d), *interior, at(end_d)], name=track.name)


def points_of(track: Track) -> Sequence[LonLat]:
    return [(p.lon, p.lat) for p in track.points]
