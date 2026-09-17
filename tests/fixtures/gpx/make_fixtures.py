"""Regenerate the synthetic GPX fixtures: ``uv run python tests/fixtures/gpx/make_fixtures.py``.

All three are built in BNG metres from a corner near Sevenoaks (E 540000, N 160000) so
their true lengths are exact by construction, then written as WGS84 lon/lat.
"""

from __future__ import annotations

from pathlib import Path

from pyproj import Transformer

HERE = Path(__file__).parent
ORIGIN = (540000.0, 160000.0)
_TO_WGS84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


def write(name: str, xy: list[tuple[float, float]], eles: list[float] | None = None) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="rambler-agent fixtures" xmlns="http://www.topografix.com/GPX/1/1">',
        f"<trk><name>{name}</name><trkseg>",
    ]
    for i, (x, y) in enumerate(xy):
        lon, lat = _TO_WGS84.transform(x, y)
        ele = f"<ele>{eles[i]:.1f}</ele>" if eles else ""
        lines.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}">{ele}</trkpt>')
    lines += ["</trkseg></trk>", "</gpx>", ""]
    (HERE / f"{name}.gpx").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ox, oy = ORIGIN
    # 1. Straight line, 1000 m due east, a point every 100 m (11 points).
    write("straight_1km", [(ox + 100.0 * i, oy) for i in range(11)])

    # 2. Closed square loop, 1000 m sides = 4000 m, a point every 100 m (41 points).
    loop: list[tuple[float, float]] = []
    loop += [(ox + 100.0 * i, oy) for i in range(10)]
    loop += [(ox + 1000.0, oy + 100.0 * i) for i in range(10)]
    loop += [(ox + 1000.0 - 100.0 * i, oy + 1000.0) for i in range(10)]
    loop += [(ox, oy + 1000.0 - 100.0 * i) for i in range(10)]
    loop.append((ox, oy))
    write("loop_4km", loop)

    # 3. Out-and-back, 2000 m east then return, a point every 50 m (81 points), with a
    #    clean elevation ramp 50 -> 150 -> 50 m: total ascent exactly 100 m.
    out = [(ox + 50.0 * i, oy) for i in range(41)]
    back = [(ox + 2000.0 - 50.0 * i, oy) for i in range(1, 41)]
    eles = [50.0 + 100.0 * i / 40 for i in range(41)] + [
        150.0 - 100.0 * i / 40 for i in range(1, 41)
    ]
    write("out_and_back_ele", out + back, eles)


if __name__ == "__main__":
    main()
