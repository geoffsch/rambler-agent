"""The ``WalkSource`` protocol every catalogue implementation must fit (plan.md D16).

Written before the SWC code so that SWC cannot quietly define the schema. A source
provides three things: a way to list walks, a way to fetch one walk's raw material,
and a way to parse that material into the shared :class:`~rambler.models.Walk`.
Everything else (geometry stats, storage, elevation backfill) is generic and lives in
:mod:`rambler.sources.ingest`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from rambler.models import Walk


@dataclass(frozen=True, slots=True)
class WalkRef:
    """Enough to identify and fetch one walk; ``listing`` carries index-page metadata."""

    source: str
    source_id: str
    slug: str
    url: str
    listing: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RawWalk:
    """Unparsed material for one walk. ``page`` and ``gpx`` may be absent for thin sources."""

    ref: WalkRef
    page: str | None = None
    gpx: str | None = None
    gpx_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class WalkSource(Protocol):
    source_id: str

    def iter_walk_refs(self) -> Iterable[WalkRef]:
        """List every walk the source publishes (cheap: index pages only)."""
        ...

    def fetch_walk(self, ref: WalkRef, *, force_refresh: bool = False) -> RawWalk:
        """Fetch one walk's page/GPX through the shared cached HTTP client."""
        ...

    def parse_walk(self, raw: RawWalk) -> Walk:
        """Turn raw material into a :class:`Walk`. Must not do network I/O."""
        ...
