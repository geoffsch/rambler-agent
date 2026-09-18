"""Shared pydantic models (walks, variations, food stops, track stats).

Source-agnostic by design (plan.md D16): the only required fields on :class:`Walk`
are identity (``source``, ``source_id``, ``slug``, ``title``, ``url``). Everything
else is optional so that a source offering nothing but a GPX and a title can be
ingested without a schema change. ``variations`` and ``food_stops`` are optional
lists; SWC happens to fill them richly, other sources may not.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class TrackStats(BaseModel):
    """Facts about a track computed from its geometry (never quoted from text)."""

    distance_km: float = Field(ge=0)
    ascent_m: float | None = Field(default=None, ge=0, description="None if no elevation data")
    est_duration: timedelta
    has_elevation: bool
    point_count: int = Field(ge=0)


class Provenance(StrEnum):
    """How a parsed field was obtained. Hard facts must be ``structured``."""

    STRUCTURED = "structured"  # from a dedicated page element / attribute
    HEURISTIC = "heuristic"  # regex over prose; may be wrong
    LLM = "llm"  # model extraction (not used yet)
    COMPUTED = "computed"  # derived from geometry


class VariationKind(StrEnum):
    SHORTCUT = "shortcut"
    EXTENSION = "extension"
    ALT_ENDING = "alt_ending"
    ALT_START = "alt_start"
    PUB_DETOUR = "pub_detour"
    OTHER = "other"


class Variation(BaseModel):
    """A documented alternative to the main route (shortcut, early ending, detour...)."""

    label: str
    kind: VariationKind = VariationKind.OTHER
    distance_km: float | None = Field(default=None, gt=0, description="total length if stated")
    note: str = ""
    provenance: Provenance = Provenance.HEURISTIC


class FoodRole(StrEnum):
    LUNCH = "lunch"
    TEA = "tea"


class FoodStop(BaseModel):
    """A pub/cafe mentioned by the source, with whatever contact details it gave (D11)."""

    name: str
    role: FoodRole = FoodRole.LUNCH
    note: str = ""
    phone: str | None = None
    hours_note: str | None = Field(
        default=None, description="Verbatim opening note from the source; never a guarantee"
    )
    position_km: float | None = Field(default=None, ge=0, description="km into the walk if stated")
    provenance: Provenance = Provenance.HEURISTIC


class Walk(BaseModel):
    """One published walk from one source, plus computed geometry facts."""

    # identity (required)
    source: str = Field(min_length=1, description="WalkSource.source_id, e.g. 'swc'")
    source_id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)

    # published metadata (all optional)
    region: str | None = None
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    published_distance_km: float | None = Field(default=None, gt=0)
    published_ascent_m: float | None = Field(default=None, ge=0)
    published_duration_min: int | None = Field(default=None, gt=0)
    toughness: int | None = Field(default=None, ge=0, le=10)
    start_station: str | None = None
    start_crs: str | None = None
    finish_station: str | None = None
    finish_crs: str | None = None
    london_departure_crs: list[str] = Field(default_factory=list)
    seeded_journey_minutes: int | None = Field(
        default=None,
        gt=0,
        description="Approximate terminus-to-start time quoted by the source; never bookable",
    )
    seeded_journey_source: str | None = None

    # files and geometry
    gpx_url: str | None = None
    gpx_path: Path | None = None
    gpx_route_count: int | None = None
    start_lat: float | None = None
    start_lon: float | None = None
    finish_lat: float | None = None
    finish_lon: float | None = None
    computed_distance_km: float | None = Field(default=None, gt=0)
    computed_ascent_m: float | None = Field(default=None, ge=0)
    has_elevation: bool | None = None

    # rich optional content
    variations: list[Variation] = Field(default_factory=list)
    food_stops: list[FoodStop] = Field(default_factory=list)
    food_notes: str | None = Field(
        default=None, description="the source's lunch/tea prose verbatim, for the agent to quote"
    )
    directions: str | None = Field(default=None, description="whole directions text, unparsed")

    fetched_at: datetime | None = None

    @property
    def distance_km(self) -> float | None:
        """Computed distance when available (the grounded value), else the published one."""
        return self.computed_distance_km or self.published_distance_km
