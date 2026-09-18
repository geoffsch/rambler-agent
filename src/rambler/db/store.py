"""SQLite walk store (``data/walks.db``) via the stdlib ``sqlite3``; no ORM.

Schema is source-agnostic (D16): ``source`` is NOT NULL on every row and the only other
required columns are identity. Variations and food stops are child tables, so a walk with
neither is perfectly valid. ``walk_fts`` is an FTS5 index over title/summary/region/tags
for keyword search.

The query API (:meth:`WalkStore.find_walks`) makes no network calls: ``max_travel_min``
filters on the source-seeded, approximate journey time (D10).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from rambler.models import FoodStop, Variation, Walk

SCHEMA = """
CREATE TABLE IF NOT EXISTS walks (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    region TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    published_distance_km REAL,
    published_ascent_m REAL,
    published_duration_min INTEGER,
    toughness INTEGER,
    start_station TEXT,
    start_crs TEXT,
    finish_station TEXT,
    finish_crs TEXT,
    london_departure_crs_json TEXT NOT NULL DEFAULT '[]',
    seeded_journey_minutes INTEGER,
    seeded_journey_source TEXT,
    gpx_url TEXT,
    gpx_path TEXT,
    gpx_route_count INTEGER,
    start_lat REAL,
    start_lon REAL,
    finish_lat REAL,
    finish_lon REAL,
    computed_distance_km REAL,
    computed_ascent_m REAL,
    has_elevation INTEGER,
    food_notes TEXT,
    directions TEXT,
    fetched_at TEXT,
    UNIQUE (source, slug)
);
CREATE TABLE IF NOT EXISTS variations (
    id INTEGER PRIMARY KEY,
    walk_id INTEGER NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    distance_km REAL,
    note TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS food_stops (
    id INTEGER PRIMARY KEY,
    walk_id INTEGER NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    phone TEXT,
    hours_note TEXT,
    position_km REAL,
    provenance TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_variations_walk ON variations(walk_id);
CREATE INDEX IF NOT EXISTS ix_food_walk ON food_stops(walk_id);
CREATE VIRTUAL TABLE IF NOT EXISTS walk_fts USING fts5(
    walk_id UNINDEXED, title, summary, region, tags
);
"""

_WALK_COLUMNS = (
    "source", "source_id", "slug", "title", "url", "region", "tags_json", "summary",
    "published_distance_km", "published_ascent_m", "published_duration_min", "toughness",
    "start_station", "start_crs", "finish_station", "finish_crs", "london_departure_crs_json",
    "seeded_journey_minutes", "seeded_journey_source", "gpx_url", "gpx_path", "gpx_route_count",
    "computed_distance_km", "computed_ascent_m", "has_elevation", "food_notes", "directions",
    "start_lat", "start_lon", "finish_lat", "finish_lon", "fetched_at",
)  # fmt: skip


def _walk_row(walk: Walk) -> dict[str, Any]:
    return {
        "source": walk.source,
        "source_id": walk.source_id,
        "slug": walk.slug,
        "title": walk.title,
        "url": walk.url,
        "region": walk.region,
        "tags_json": json.dumps(walk.tags),
        "summary": walk.summary,
        "published_distance_km": walk.published_distance_km,
        "published_ascent_m": walk.published_ascent_m,
        "published_duration_min": walk.published_duration_min,
        "toughness": walk.toughness,
        "start_station": walk.start_station,
        "start_crs": walk.start_crs,
        "finish_station": walk.finish_station,
        "finish_crs": walk.finish_crs,
        "london_departure_crs_json": json.dumps(walk.london_departure_crs),
        "seeded_journey_minutes": walk.seeded_journey_minutes,
        "seeded_journey_source": walk.seeded_journey_source,
        "gpx_url": walk.gpx_url,
        "gpx_path": str(walk.gpx_path) if walk.gpx_path else None,
        "gpx_route_count": walk.gpx_route_count,
        "start_lat": walk.start_lat,
        "start_lon": walk.start_lon,
        "finish_lat": walk.finish_lat,
        "finish_lon": walk.finish_lon,
        "computed_distance_km": walk.computed_distance_km,
        "computed_ascent_m": walk.computed_ascent_m,
        "has_elevation": None if walk.has_elevation is None else int(walk.has_elevation),
        "food_notes": walk.food_notes,
        "directions": walk.directions,
        "fetched_at": walk.fetched_at.isoformat() if walk.fetched_at else None,
    }


class WalkStore:
    """Open (and create) the walk database. Use as a context manager or call ``close()``."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._add_missing_columns("walks")

    def _add_missing_columns(self, table: str) -> None:
        """Add any column in :data:`SCHEMA` that an older database file lacks.

        ``CREATE TABLE IF NOT EXISTS`` leaves an existing table untouched, so without this
        a new optional field turns every insert into a silent failure. Additive only: a
        renamed or retyped column still needs a rebuild, which is cheap because ingestion
        is idempotent over the HTTP cache.
        """
        existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in _declared_columns(table).items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self.conn.commit()

    def __enter__(self) -> WalkStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ writes

    def upsert(self, walk: Walk) -> int:
        """Insert or replace a walk (keyed on ``source`` + ``slug``); returns its row id."""
        row = _walk_row(walk)
        cols = ", ".join(_WALK_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _WALK_COLUMNS)
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in _WALK_COLUMNS if c not in ("source", "slug")
        )
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO walks ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(source, slug) DO UPDATE SET {updates} RETURNING id",
                row,
            )
            walk_id = int(cur.fetchone()[0])
            self.conn.execute("DELETE FROM variations WHERE walk_id = ?", (walk_id,))
            self.conn.execute("DELETE FROM food_stops WHERE walk_id = ?", (walk_id,))
            self.conn.executemany(
                "INSERT INTO variations"
                " (walk_id, position, label, kind, distance_km, note, provenance)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (walk_id, i, v.label, v.kind.value, v.distance_km, v.note, v.provenance.value)
                    for i, v in enumerate(walk.variations)
                ],
            )
            self.conn.executemany(
                "INSERT INTO food_stops (walk_id, position, name, role, note, phone, hours_note,"
                " position_km, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        walk_id,
                        i,
                        f.name,
                        f.role.value,
                        f.note,
                        f.phone,
                        f.hours_note,
                        f.position_km,
                        f.provenance.value,
                    )
                    for i, f in enumerate(walk.food_stops)
                ],
            )
            self.conn.execute("DELETE FROM walk_fts WHERE walk_id = ?", (walk_id,))
            self.conn.execute(
                "INSERT INTO walk_fts (walk_id, title, summary, region, tags)"
                " VALUES (?, ?, ?, ?, ?)",
                (walk_id, walk.title, walk.summary or "", walk.region or "", " ".join(walk.tags)),
            )
        return walk_id

    # ------------------------------------------------------------------ reads

    def _hydrate(self, row: sqlite3.Row) -> Walk:
        walk_id = row["id"]
        variations = [
            Variation(
                label=r["label"],
                kind=r["kind"],
                distance_km=r["distance_km"],
                note=r["note"],
                provenance=r["provenance"],
            )
            for r in self.conn.execute(
                "SELECT * FROM variations WHERE walk_id = ? ORDER BY position", (walk_id,)
            )
        ]
        food = [
            FoodStop(
                name=r["name"],
                role=r["role"],
                note=r["note"],
                phone=r["phone"],
                hours_note=r["hours_note"],
                position_km=r["position_km"],
                provenance=r["provenance"],
            )
            for r in self.conn.execute(
                "SELECT * FROM food_stops WHERE walk_id = ? ORDER BY position", (walk_id,)
            )
        ]
        d = dict(row)
        return Walk(
            source=d["source"],
            source_id=d["source_id"],
            slug=d["slug"],
            title=d["title"],
            url=d["url"],
            region=d["region"],
            tags=json.loads(d["tags_json"]),
            summary=d["summary"],
            published_distance_km=d["published_distance_km"],
            published_ascent_m=d["published_ascent_m"],
            published_duration_min=d["published_duration_min"],
            toughness=d["toughness"],
            start_station=d["start_station"],
            start_crs=d["start_crs"],
            finish_station=d["finish_station"],
            finish_crs=d["finish_crs"],
            london_departure_crs=json.loads(d["london_departure_crs_json"]),
            seeded_journey_minutes=d["seeded_journey_minutes"],
            seeded_journey_source=d["seeded_journey_source"],
            gpx_url=d["gpx_url"],
            gpx_path=Path(d["gpx_path"]) if d["gpx_path"] else None,
            gpx_route_count=d["gpx_route_count"],
            start_lat=d["start_lat"],
            start_lon=d["start_lon"],
            finish_lat=d["finish_lat"],
            finish_lon=d["finish_lon"],
            computed_distance_km=d["computed_distance_km"],
            computed_ascent_m=d["computed_ascent_m"],
            has_elevation=None if d["has_elevation"] is None else bool(d["has_elevation"]),
            variations=variations,
            food_stops=food,
            food_notes=d["food_notes"],
            directions=d["directions"],
            fetched_at=datetime.fromisoformat(d["fetched_at"]) if d["fetched_at"] else None,
        )

    def get_walk(self, slug: str, source: str | None = None) -> Walk | None:
        sql = "SELECT * FROM walks WHERE slug = ?"
        params: list[Any] = [slug]
        if source:
            sql += " AND source = ?"
            params.append(source)
        row = self.conn.execute(sql, params).fetchone()
        return self._hydrate(row) if row else None

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM walks").fetchone()[0])

    def all_walks(self) -> list[Walk]:
        return [self._hydrate(r) for r in self.conn.execute("SELECT * FROM walks ORDER BY slug")]

    def find_walks(
        self,
        *,
        max_km: float | None = None,
        min_km: float | None = None,
        max_ascent_m: float | None = None,
        max_toughness: int | None = None,
        regions: Iterable[str] | None = None,
        london_crs_in: Iterable[str] | None = None,
        max_travel_min: int | None = None,
        text: str | None = None,
        source: str | None = None,
        limit: int = 200,
    ) -> list[Walk]:
        """Filter walks. Distance uses the computed value when present, else published.

        ``london_crs_in``: keep walks reachable from any of these London termini (CRS).
        ``max_travel_min``: uses the source-seeded approximate time; walks with no seeded
        time are excluded. No network.
        ``text``: FTS5 match over title/summary/region/tags.
        """
        where: list[str] = []
        params: list[Any] = []
        dist = "COALESCE(w.computed_distance_km, w.published_distance_km)"
        if max_km is not None:
            where.append(f"{dist} <= ?")
            params.append(max_km)
        if min_km is not None:
            where.append(f"{dist} >= ?")
            params.append(min_km)
        if max_ascent_m is not None:
            where.append("COALESCE(w.computed_ascent_m, w.published_ascent_m) <= ?")
            params.append(max_ascent_m)
        if max_toughness is not None:
            where.append("w.toughness <= ?")
            params.append(max_toughness)
        if regions:
            rs = [r.lower() for r in regions]
            where.append(f"LOWER(w.region) IN ({', '.join('?' * len(rs))})")
            params.extend(rs)
        if london_crs_in:
            crs = [c.upper() for c in london_crs_in]
            marks = ", ".join("?" * len(crs))
            where.append(
                "EXISTS (SELECT 1 FROM json_each(w.london_departure_crs_json) j"
                f" WHERE j.value IN ({marks}))"
            )
            params.extend(crs)
        if max_travel_min is not None:
            where.append("w.seeded_journey_minutes IS NOT NULL AND w.seeded_journey_minutes <= ?")
            params.append(max_travel_min)
        if source:
            where.append("w.source = ?")
            params.append(source)
        join = ""
        if text:
            join = " JOIN walk_fts f ON f.walk_id = w.id"
            where.append("walk_fts MATCH ?")
            params.append(_fts_query(text))
        sql = f"SELECT w.* FROM walks w{join}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {dist} ASC, w.slug LIMIT ?"
        params.append(limit)
        return [self._hydrate(r) for r in self.conn.execute(sql, params)]

    def stats(self) -> dict[str, Any]:
        """Counts and coverage figures for ``rambler walks stats``."""
        q = self.conn.execute
        total = self.count()
        if total == 0:
            return {"total": 0}
        one = lambda sql: q(sql).fetchone()[0]  # noqa: E731
        mismatch = q(
            "SELECT slug, published_distance_km, computed_distance_km FROM walks"
            " WHERE published_distance_km IS NOT NULL AND computed_distance_km IS NOT NULL"
            " AND ABS(computed_distance_km - published_distance_km) / published_distance_km"
            " > 0.10 ORDER BY ABS(computed_distance_km - published_distance_km)"
            " / published_distance_km DESC"
        ).fetchall()
        hist = q(
            "SELECT CAST(COALESCE(computed_distance_km, published_distance_km) / 2 AS INTEGER) * 2"
            " AS b, COUNT(*) FROM walks"
            " WHERE COALESCE(computed_distance_km, published_distance_km) IS NOT NULL"
            " GROUP BY b ORDER BY b"
        ).fetchall()
        return {
            "total": total,
            "by_source": dict(q("SELECT source, COUNT(*) FROM walks GROUP BY source").fetchall()),
            "with_gpx": one("SELECT COUNT(*) FROM walks WHERE gpx_path IS NOT NULL"),
            "with_computed_distance": one(
                "SELECT COUNT(*) FROM walks WHERE computed_distance_km IS NOT NULL"
            ),
            "with_elevation": one("SELECT COUNT(*) FROM walks WHERE has_elevation = 1"),
            "with_seeded_journey": one(
                "SELECT COUNT(*) FROM walks WHERE seeded_journey_minutes IS NOT NULL"
            ),
            "with_start_crs": one("SELECT COUNT(*) FROM walks WHERE start_crs IS NOT NULL"),
            "with_start_point": one("SELECT COUNT(*) FROM walks WHERE start_lat IS NOT NULL"),
            "with_variations": one("SELECT COUNT(DISTINCT walk_id) FROM variations"),
            "with_variation_distance": one(
                "SELECT COUNT(DISTINCT walk_id) FROM variations WHERE distance_km IS NOT NULL"
            ),
            "with_food_stops": one("SELECT COUNT(DISTINCT walk_id) FROM food_stops"),
            "with_food_phone": one(
                "SELECT COUNT(DISTINCT walk_id) FROM food_stops WHERE phone IS NOT NULL"
            ),
            "with_directions": one("SELECT COUNT(*) FROM walks WHERE directions IS NOT NULL"),
            "distance_mismatch_over_10pct": [tuple(r) for r in mismatch],
            "distance_histogram_2km": [(int(b), int(n)) for b, n in hist],
        }


def _declared_columns(table: str) -> dict[str, str]:
    """``{column: sql_type}`` parsed out of :data:`SCHEMA`, so migration cannot drift."""
    body = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\);", SCHEMA, re.S)
    if body is None:  # pragma: no cover - only reachable if SCHEMA is edited badly
        raise ValueError(f"no CREATE TABLE for {table} in SCHEMA")
    columns: dict[str, str] = {}
    for raw in body.group(1).splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.upper().startswith(("UNIQUE", "PRIMARY", "FOREIGN", "CHECK")):
            continue
        name, _, decl = line.partition(" ")
        columns[name] = decl
    return columns


def _fts_query(text: str) -> str:
    """Quote each word so punctuation in user text cannot break the FTS5 grammar."""
    words = [w.replace('"', "") for w in text.split()]
    return " ".join(f'"{w}"' for w in words if w)


def walk_sequence_to_ids(store: WalkStore, walks: Sequence[Walk]) -> list[int]:  # pragma: no cover
    return [store.upsert(w) for w in walks]
