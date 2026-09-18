"""Generic ingestion pipeline: ``WalkSource`` -> geometry facts -> ``WalkStore`` + GPX files.

Source-agnostic on purpose. Per walk: fetch (cached), parse, save the GPX under
``<gpx_dir>/<source>/<slug>.gpx``, compute distance/ascent from the *main* route with
the geo core (backfilling elevation from Open-Meteo when the file has none), then upsert.
A failing walk is reported and skipped; the batch continues (HTML drift must never
silently drop a catalogue).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from rambler.db.store import WalkStore
from rambler.geo import describe
from rambler.geo.elevation import backfill_elevation
from rambler.geo.gpx import parse_gpx_all
from rambler.models import Walk
from rambler.sources.base import WalkSource

Progress = Callable[[int, int, str], None]


@dataclass
class IngestReport:
    total: int = 0
    ingested: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    without_gpx: list[str] = field(default_factory=list)
    geometry_failed: list[tuple[str, str]] = field(default_factory=list)
    """Walk stored without computed stats because its GPX could not be processed."""
    elevation_backfilled: list[str] = field(default_factory=list)
    distance_mismatch: list[tuple[str, float, float]] = field(default_factory=list)
    """(slug, published_km, computed_km) where they differ by more than 10%."""

    def summary(self) -> str:
        lines = [
            f"walks listed: {self.total}",
            f"ingested:     {self.ingested}",
            f"failed:       {len(self.failed)}",
            f"without GPX:  {len(self.without_gpx)}",
            f"GPX stored but geometry failed: {len(self.geometry_failed)}",
            f"elevation backfilled from Open-Meteo: {len(self.elevation_backfilled)}",
            f"published vs computed distance >10% apart: {len(self.distance_mismatch)}",
        ]
        for slug, err in self.failed[:20]:
            lines.append(f"  ! {slug}: {err}")
        for slug, err in self.geometry_failed[:20]:
            lines.append(f"  ~ {slug}: {err}")
        return "\n".join(lines)


def ingest(
    source: WalkSource,
    store: WalkStore,
    *,
    gpx_dir: Path,
    limit: int | None = None,
    force_refresh: bool = False,
    backfill: bool = True,
    client: httpx.Client | None = None,
    progress: Progress | None = None,
) -> IngestReport:
    """Ingest every walk the source lists (or the first ``limit``). Idempotent over the cache."""
    refs = list(source.iter_walk_refs())
    if limit:
        refs = refs[:limit]
    report = IngestReport(total=len(refs))
    for i, ref in enumerate(refs, 1):
        if progress:
            progress(i, len(refs), ref.slug)
        try:
            raw = source.fetch_walk(ref, force_refresh=force_refresh)
            walk = source.parse_walk(raw)
            if raw.gpx:
                try:
                    _attach_geometry(walk, raw.gpx, gpx_dir, backfill, client, report)
                except Exception as exc:  # e.g. a walk abroad: keep the walk, drop the stats
                    report.geometry_failed.append((walk.slug, f"{type(exc).__name__}: {exc}"))
            else:
                report.without_gpx.append(walk.slug)
            store.upsert(walk)
            report.ingested += 1
        except Exception as exc:  # per-walk failures must not stop the batch
            report.failed.append((ref.slug, f"{type(exc).__name__}: {exc}"))
    return report


def _attach_geometry(
    walk: Walk,
    gpx_text: str,
    gpx_dir: Path,
    backfill: bool,
    client: httpx.Client | None,
    report: IngestReport,
) -> None:
    path = gpx_dir / walk.source / f"{walk.slug}.gpx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gpx_text, encoding="utf-8")
    walk.gpx_path = path
    tracks = parse_gpx_all(gpx_text, source_path=path)
    main = tracks[0]
    walk.gpx_route_count = len(tracks)
    # Store the end points in plain lon/lat before any projection, so they survive even
    # for walks outside Great Britain (plans 03-05 need them for stations, POIs and maps).
    walk.start_lat, walk.start_lon = main.points[0].lat, main.points[0].lon
    walk.finish_lat, walk.finish_lon = main.points[-1].lat, main.points[-1].lon
    if backfill and not main.has_elevation:
        main = backfill_elevation(main, client)
        report.elevation_backfilled.append(walk.slug)
    stats = describe(main)
    walk.computed_distance_km = round(stats.distance_km, 2)
    walk.computed_ascent_m = round(stats.ascent_m) if stats.ascent_m is not None else None
    walk.has_elevation = stats.has_elevation
    pub = walk.published_distance_km
    if pub and abs(stats.distance_km - pub) / pub > 0.10:
        report.distance_mismatch.append((walk.slug, pub, walk.computed_distance_km))
