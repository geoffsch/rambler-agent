"""Shared pydantic models (walks, variations, POIs, journeys).

Populated incrementally: plan 01 adds :class:`TrackStats`; plan 02 adds the walk model.
Nothing in here may assume a particular walk source (plan.md D16).
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, Field


class TrackStats(BaseModel):
    """Facts about a track computed from its geometry (never quoted from text)."""

    distance_km: float = Field(ge=0)
    ascent_m: float | None = Field(default=None, ge=0, description="None if no elevation data")
    est_duration: timedelta
    has_elevation: bool
    point_count: int = Field(ge=0)
