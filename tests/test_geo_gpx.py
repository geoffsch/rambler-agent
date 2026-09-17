from pathlib import Path

import pytest

from rambler.geo.gpx import Track, TrackPoint, load_track, parse_gpx, parse_gpx_all, to_gpx

pytestmark = pytest.mark.unit

ROUTE_ONLY = """<?xml version="1.0"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">
<rte><name>r</name>
<rtept lat="51.30" lon="0.10"><ele>10</ele></rtept>
<rtept lat="51.31" lon="0.11"></rtept>
</rte></gpx>"""

MAIN_PLUS_OPTION = """<?xml version="1.0"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">
<rte><name> Walk 43 Main </name>
<rtept lat="51.30" lon="0.10"/><rtept lat="51.31" lon="0.11"/><rtept lat="51.32" lon="0.12"/>
</rte>
<rte><name>Pub detour option</name>
<rtept lat="51.31" lon="0.11"/><rtept lat="51.315" lon="0.115"/>
</rte>
<rte><name>empty</name></rte>
</gpx>"""

TWO_SEGMENTS = """<?xml version="1.0"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">
<trk><name>a</name>
<trkseg><trkpt lat="51.30" lon="0.10"/><trkpt lat="51.31" lon="0.11"/></trkseg>
<trkseg><trkpt lat="51.32" lon="0.12"/></trkseg>
</trk></gpx>"""


def test_load_fixtures(fixtures_dir: Path) -> None:
    straight = load_track(fixtures_dir / "gpx" / "straight_1km.gpx")
    assert len(straight) == 11
    assert straight.name == "straight_1km"
    assert straight.source_path == fixtures_dir / "gpx" / "straight_1km.gpx"
    assert not straight.has_elevation

    oab = load_track(fixtures_dir / "gpx" / "out_and_back_ele.gpx")
    assert len(oab) == 81
    assert oab.has_elevation
    assert oab.points[0].ele == 50.0
    assert oab.points[40].ele == 150.0


def test_segments_are_concatenated_in_order() -> None:
    t = parse_gpx(TWO_SEGMENTS)
    assert [p.lat for p in t.points] == [51.30, 51.31, 51.32]


def test_multiple_routes_are_separate_tracks() -> None:
    """SWC files: first route is the main walk, later routes are documented options."""
    tracks = parse_gpx_all(MAIN_PLUS_OPTION)
    assert [t.name for t in tracks] == ["Walk 43 Main", "Pub detour option"]
    assert [len(t) for t in tracks] == [3, 2]
    main = parse_gpx(MAIN_PLUS_OPTION)
    assert main.name == "Walk 43 Main" and len(main) == 3, "parse_gpx must not merge options in"


def test_routes_used_when_no_tracks() -> None:
    t = parse_gpx(ROUTE_ONLY)
    assert len(t) == 2
    assert t.name == "r"
    assert not t.has_elevation, "one point lacks elevation"


def test_empty_gpx_raises() -> None:
    with pytest.raises(ValueError, match="no track or route points"):
        parse_gpx('<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"/>')


def test_to_gpx_round_trips() -> None:
    t = Track([TrackPoint(0.1, 51.3, 12.5), TrackPoint(0.11, 51.31, None)], name="n")
    back = parse_gpx(to_gpx(t))
    assert back.name == "n"
    assert back.points == t.points
