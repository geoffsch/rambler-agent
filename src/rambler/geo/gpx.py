"""GPX parsing into a plain, source-agnostic :class:`Track`.

A track is an ordered list of lon/lat points with optional elevation. One GPX file
can hold several ``<trk>`` and ``<rte>`` elements, and they are **not** the same walk:
Saturday Walkers Club files carry the main walk as the first route and each documented
option (pub detour, short cut) as a further route. So :func:`parse_gpx_all` returns one
:class:`Track` per ``<trk>``/``<rte>`` (segments within a track are concatenated), and
:func:`parse_gpx` returns the first. Waypoints are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import gpxpy
import gpxpy.gpx


@dataclass(frozen=True, slots=True)
class TrackPoint:
    lon: float
    lat: float
    ele: float | None = None


@dataclass(slots=True)
class Track:
    points: list[TrackPoint] = field(default_factory=list)
    name: str | None = None
    source_path: Path | None = None

    @property
    def has_elevation(self) -> bool:
        """True when every point carries an elevation (and there is at least one point)."""
        return bool(self.points) and all(p.ele is not None for p in self.points)

    def __len__(self) -> int:
        return len(self.points)


def parse_gpx_all(text: str, *, source_path: Path | None = None) -> list[Track]:
    """One :class:`Track` per ``<trk>`` (segments concatenated) then per ``<rte>``, file order.

    Empty tracks/routes are dropped. Raises ``ValueError`` if nothing remains.
    """
    gpx = gpxpy.parse(text)
    tracks: list[Track] = []
    for trk in gpx.tracks:
        points = [
            TrackPoint(p.longitude, p.latitude, p.elevation)
            for seg in trk.segments
            for p in seg.points
        ]
        if points:
            tracks.append(Track(points, name=_clean(trk.name) or gpx.name, source_path=source_path))
    for rte in gpx.routes:
        points = [TrackPoint(p.longitude, p.latitude, p.elevation) for p in rte.points]
        if points:
            tracks.append(Track(points, name=_clean(rte.name) or gpx.name, source_path=source_path))
    if not tracks:
        raise ValueError(f"GPX contains no track or route points ({source_path or 'text'})")
    return tracks


def parse_gpx(text: str, *, source_path: Path | None = None) -> Track:
    """The first track (or route) in a GPX file: the main walk, by SWC convention."""
    return parse_gpx_all(text, source_path=source_path)[0]


def load_tracks(path: Path | str) -> list[Track]:
    """Read every track/route from a GPX file on disk."""
    path = Path(path)
    return parse_gpx_all(path.read_text(encoding="utf-8"), source_path=path)


def load_track(path: Path | str) -> Track:
    """Read the first track/route from a GPX file on disk."""
    return load_tracks(path)[0]


def _clean(name: str | None) -> str | None:
    return name.strip() if name else None


def to_gpx(track: Track, *, name: str | None = None) -> str:
    """Serialise a track back to GPX XML (single track, single segment)."""
    gpx = gpxpy.gpx.GPX()
    gpx.creator = "rambler-agent"
    trk = gpxpy.gpx.GPXTrack(name=name or track.name)
    seg = gpxpy.gpx.GPXTrackSegment()
    seg.points = [
        gpxpy.gpx.GPXTrackPoint(latitude=p.lat, longitude=p.lon, elevation=p.ele)
        for p in track.points
    ]
    trk.segments.append(seg)
    gpx.tracks.append(trk)
    return gpx.to_xml()
