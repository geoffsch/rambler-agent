"""Walk duration estimates.

Naismith's rule: 12 minutes per kilometre plus 1 minute per 10 m of ascent, for a fit
adult walking steadily. We scale that by a pace factor from the user profile (default
1.4 for a family with young kids) and add a fixed lunch stop. This is advice, not
gospel: it ignores terrain, mud, stiles, and how the day is going.
"""

from __future__ import annotations

from datetime import timedelta

from rambler.config import PaceProfile, UserProfile
from rambler.geo.elevation import total_ascent_m
from rambler.geo.geometry import track_length_km
from rambler.geo.gpx import Track

NAISMITH_MIN_PER_KM = 12.0
NAISMITH_MIN_PER_M_ASCENT = 0.1  # 1 minute per 10 m


def naismith_minutes(distance_km: float, ascent_m: float | None = None) -> float:
    """Unscaled Naismith walking time in minutes."""
    return distance_km * NAISMITH_MIN_PER_KM + (ascent_m or 0.0) * NAISMITH_MIN_PER_M_ASCENT


def estimate_minutes(
    distance_km: float,
    ascent_m: float | None = None,
    *,
    pace: PaceProfile | None = None,
) -> float:
    """Naismith scaled by ``pace.pace_factor`` plus ``pace.lunch_stop_minutes``."""
    pace = pace or PaceProfile()
    return naismith_minutes(distance_km, ascent_m) * pace.pace_factor + pace.lunch_stop_minutes


def estimate_duration(track: Track, profile: UserProfile | None = None) -> timedelta:
    """Estimated door-to-door walking time for a track under the profile's pace settings."""
    pace = profile.pace if profile else PaceProfile()
    minutes = estimate_minutes(track_length_km(track), total_ascent_m(track), pace=pace)
    return timedelta(minutes=round(minutes))
