"""Approximate door-to-door travel, with zero API calls (plan.md D10).

Real journey planning arrives in plan 03 and will be called only for the handful of
walks that shortlist. Until then -- and afterwards, as the free pre-filter that keeps us
inside the TransportAPI free tier -- travel time is estimated by adding two approximate
numbers:

* **access**: home station to a London terminus, taken from the user profile;
* **onward**: that terminus to the walk's start, seeded from the source's own listing.

Both are approximate and the result is **never bookable**. Two honest caveats: the
source quotes one journey time per walk even when it lists several termini, so the
onward leg may not correspond to the terminus we picked; and neither number includes
the wait at the terminus.

A terminus the profile does not list is *not* excluded. It is ranked last and flagged,
because "I would not normally go via Waterloo" is a preference, not a rule, and a good
enough walk overturns it.

Where no time can be estimated at all -- two thirds of the catalogue, because the source
only sometimes quotes one -- walks are ordered by crow-flies distance from London rather
than pretending. That keeps Kent above Devon without inventing a train speed. The
fallback deliberately uses great-circle distance rather than the project's usual
EPSG:27700 maths, because a handful of walks lie outside Great Britain entirely.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from rambler.config import Station, UserProfile
from rambler.models import Walk

#: Roughly central London (Charing Cross), the origin for the crow-flies fallback.
LONDON_CENTRE = (51.5074, -0.1276)
EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True, slots=True)
class AccessEstimate:
    """How awkward this walk is to reach from one home station."""

    terminus: str | None
    """The terminus we would use: the quickest listed one, else the walk's first."""
    access_minutes: int | None
    """Home station to terminus, from the profile."""
    onward_minutes: int | None
    """Terminus to walk start, seeded from the source (approximate)."""
    preferred: bool
    """True when the walk departs from a terminus listed in the profile."""

    @property
    def total_minutes(self) -> int | None:
        """Approximate door-to-door minutes, or ``None`` when either leg is unknown."""
        if self.access_minutes is None or self.onward_minutes is None:
            return None
        return self.access_minutes + self.onward_minutes

    def describe(self) -> str:
        """Compact rendering, e.g. ``"12+35=47"`` or ``"?"``."""
        if self.total_minutes is None:
            return "?" if self.onward_minutes is None else f"?+{self.onward_minutes}"
        return f"{self.access_minutes}+{self.onward_minutes}={self.total_minutes}"


def estimate_access(walk: Walk, station: Station) -> AccessEstimate:
    """Estimate travel to ``walk`` from ``station``, via the quickest terminus it lists."""
    listed = [t for t in walk.london_departure_crs if station.prefers(t)]
    if listed:
        # quickest known first; termini with an unknown time sort after those with one
        terminus = min(
            listed,
            key=lambda t: (station.access_minutes(t) is None, station.access_minutes(t) or 0),
        )
        return AccessEstimate(
            terminus=terminus,
            access_minutes=station.access_minutes(terminus),
            onward_minutes=walk.seeded_journey_minutes,
            preferred=True,
        )
    return AccessEstimate(
        terminus=next(iter(walk.london_departure_crs), None),
        access_minutes=None,
        onward_minutes=walk.seeded_journey_minutes,
        preferred=False,
    )


def crow_km_from_london(walk: Walk) -> float | None:
    """Great-circle kilometres from central London to the walk's start point."""
    if walk.start_lat is None or walk.start_lon is None:
        return None
    lat1, lon1 = (math.radians(x) for x in LONDON_CENTRE)
    lat2, lon2 = math.radians(walk.start_lat), math.radians(walk.start_lon)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def rank_key(walk: Walk, estimate: AccessEstimate) -> tuple[int, int, float]:
    """Sort key: preferred termini first, then known travel time, then distance from London.

    The third element only decides ties among walks whose travel time is unknown; without
    it they would be ordered by walk length, floating Devon above Kent.
    """
    total = estimate.total_minutes
    return (
        0 if estimate.preferred else 1,
        total if total is not None else 10_000,
        crow_km_from_london(walk) or 10_000.0,
    )


def within_travel_budget(estimate: AccessEstimate, max_minutes: int | None) -> bool:
    """Keep a walk unless its *known* estimate exceeds the budget.

    Unknown times are kept, not dropped: only a third of walks carry a seeded time, so
    discarding them would hide most of the catalogue behind missing data.
    """
    if max_minutes is None:
        return True
    total = estimate.total_minutes
    return total is None or total <= max_minutes


def estimates_for(
    walks: list[Walk], profile: UserProfile, home_crs: str
) -> list[tuple[Walk, AccessEstimate]]:
    """Pair each walk with its access estimate from one home station. Raises if unknown."""
    station = profile.station(home_crs)
    if station is None:
        raise ValueError(f"{home_crs.upper()} is not a home station in the profile")
    return [(w, estimate_access(w, station)) for w in walks]
