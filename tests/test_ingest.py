from pathlib import Path

import pytest

from rambler.db.store import WalkStore
from rambler.models import Walk
from rambler.sources.base import RawWalk, WalkRef, WalkSource
from rambler.sources.ingest import ingest
from rambler.sources.swc import parse_listing, parse_walk_page

pytestmark = pytest.mark.unit


class FixtureSource:
    """A WalkSource over the synthetic fixtures: no network, one walk deliberately broken."""

    source_id = "swc"

    def __init__(self, swc_dir: Path, abroad: bool = False) -> None:
        self.dir = swc_dir
        self.abroad = abroad

    def iter_walk_refs(self):
        return parse_listing((self.dir / "listing.html").read_text(encoding="utf-8"))

    def fetch_walk(self, ref: WalkRef, *, force_refresh: bool = False) -> RawWalk:
        if ref.slug == "ashwell-circular":
            return RawWalk(
                ref=ref,
                page=(self.dir / "walk_old.html").read_text(encoding="utf-8"),
                gpx=(self.dir / "walk.gpx").read_text(encoding="utf-8"),
                gpx_url="https://example.invalid/walk.gpx",
            )
        if ref.slug == "bramley-to-cranfold":
            return RawWalk(ref=ref, page=(self.dir / "walk_new.html").read_text(encoding="utf-8"))
        if ref.slug == "dunmere-loop" and self.abroad:
            gpx = (self.dir / "walk.gpx").read_text(encoding="utf-8")
            gpx = gpx.replace('lat="51.32', 'lat="37.09').replace('lon="0.0', 'lon="-8.3')
            return RawWalk(ref=ref, page="<h1>Dunmere Loop Walk</h1>", gpx=gpx)
        raise RuntimeError("site returned 500")

    def parse_walk(self, raw: RawWalk) -> Walk:
        walk = parse_walk_page(raw.page, raw.ref)
        walk.gpx_url = raw.gpx_url
        return walk


def test_ingest_pipeline_offline(fixtures_dir: Path, tmp_path: Path) -> None:
    src = FixtureSource(fixtures_dir / "swc")
    assert isinstance(src, WalkSource)
    with WalkStore(tmp_path / "walks.db") as store:
        report = ingest(src, store, gpx_dir=tmp_path / "gpx", backfill=False)
        assert (report.total, report.ingested) == (3, 2)
        assert report.failed == [("dunmere-loop", "RuntimeError: site returned 500")]
        assert report.without_gpx == ["bramley-to-cranfold"]
        assert report.elevation_backfilled == []

        w = store.get_walk("ashwell-circular")
        assert w.gpx_path == tmp_path / "gpx" / "swc" / "ashwell-circular.gpx"
        assert w.gpx_path.exists()
        assert w.gpx_route_count == 2, "the pub-detour route is kept separate, not merged"
        assert w.has_elevation is True
        assert w.computed_distance_km == pytest.approx(0.4, abs=0.01), "main route only"
        assert w.computed_ascent_m is not None and 0 < w.computed_ascent_m <= 20
        # the fixture GPX is 0.4 km against 9.87 km published: flagged, not hidden
        assert report.distance_mismatch == [("ashwell-circular", 9.87, w.computed_distance_km)]

        # idempotent: a second run upserts, no duplicates
        ingest(src, store, gpx_dir=tmp_path / "gpx", backfill=False)
        assert store.count() == 2
        assert "walks listed: 3" in report.summary()


def test_geometry_failure_keeps_the_walk(fixtures_dir: Path, tmp_path: Path) -> None:
    src = FixtureSource(fixtures_dir / "swc", abroad=True)
    with WalkStore() as store:
        report = ingest(src, store, gpx_dir=tmp_path / "gpx", backfill=False)
        assert report.ingested == 3 and report.failed == []
        assert report.geometry_failed[0][0] == "dunmere-loop"
        assert "outside Great Britain" in report.geometry_failed[0][1]
        w = store.get_walk("dunmere-loop")
        assert w.gpx_path is not None and w.gpx_path.exists(), "the file is still kept"
        assert w.computed_distance_km is None and w.computed_ascent_m is None
        assert w.published_distance_km == 21.0
        assert "geometry failed: 1" in report.summary()
