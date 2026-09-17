"""GPX parsing, EPSG:27700 geometry, corridor checks, elevation and timing (plan 01).

Pure functions over :class:`rambler.geo.gpx.Track`. The only network call is
:func:`rambler.geo.elevation.backfill_elevation`.
"""

from __future__ import annotations

from rambler.config import UserProfile
from rambler.geo.elevation import total_ascent_m
from rambler.geo.geometry import track_length_km
from rambler.geo.gpx import Track, TrackPoint, load_track, parse_gpx, to_gpx
from rambler.geo.timing import estimate_duration
from rambler.models import TrackStats

__all__ = [
    "Track",
    "TrackPoint",
    "TrackStats",
    "describe",
    "load_track",
    "parse_gpx",
    "to_gpx",
]


def describe(track: Track, profile: UserProfile | None = None) -> TrackStats:
    """Distance, ascent and estimated duration for a track, all computed from geometry."""
    return TrackStats(
        distance_km=track_length_km(track),
        ascent_m=total_ascent_m(track),
        est_duration=estimate_duration(track, profile),
        has_elevation=track.has_elevation,
        point_count=len(track.points),
    )
