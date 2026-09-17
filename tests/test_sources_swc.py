from pathlib import Path

import httpx
import pytest

from rambler.config import Settings
from rambler.http import get_client
from rambler.models import FoodRole, Provenance, VariationKind
from rambler.sources.base import WalkSource
from rambler.sources.swc import (
    SwcSource,
    classify_variation,
    estimate_variation_km,
    parse_gpx_url,
    parse_listing,
    parse_minutes,
    parse_walk_page,
    text_of,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def swc_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "swc"


@pytest.fixture
def refs(swc_dir: Path):
    return parse_listing((swc_dir / "listing.html").read_text(encoding="utf-8"))


def test_text_of_handles_fractions_and_entities() -> None:
    assert text_of("<b>13¼ km</b> &amp; ½ mile") == "13.25 km & 0.5 mile"


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("3 hours 30 minutes", 210),
        ("Three hours 20 minutes walking time", 200),
        ("Four hours walking time", 240),
        ("about 1 hour 5 minutes", 65),
        ("two and a half hours", 150),
        ("Journey time 42 minutes", 42),
        ("an hour and a half", 90),
        ("an hour and a quarter", 75),
        ("half an hour", 30),
        ("journey time, just over an hour", 65),
        ("just under an hour", 55),
        ("is between 30 and 34 minutes", 34),
        ("a journey time of 19 to 26 mins", 26),
        ("40-50 minutes", 50),
        ("no time here", None),
    ],
)
def test_parse_minutes(text: str, minutes: int | None) -> None:
    assert parse_minutes(text) == minutes


def test_parse_listing(refs) -> None:
    assert [r.slug for r in refs] == ["ashwell-circular", "bramley-to-cranfold", "dunmere-loop"]
    a = refs[0]
    assert a.source == "swc" and a.source_id == "cw1.7"
    assert a.url == "https://www.walkingclub.org.uk/walk/ashwell-circular/"
    assert a.listing["title"] == "Ashwell Circular via Fakeham"
    assert a.listing["region"] == "Kent"
    assert a.listing["km"] == 9.9
    assert a.listing["effort"] == 3
    assert a.listing["ascent_m"] == 190.0
    assert a.listing["london_departure_crs"] == ["BFR", "VIC"]
    assert a.listing["tags"] == ["Chalk Downs", "My Favourites"]
    assert refs[1].source_id == "swc.512"
    assert refs[2].listing["london_departure_crs"] == ["WAT"]


def test_parse_old_generation_page(swc_dir: Path, refs) -> None:
    walk = parse_walk_page((swc_dir / "walk_old.html").read_text(encoding="utf-8"), refs[0])
    assert walk.source == "swc" and walk.source_id == "cw1.7" and walk.slug == "ashwell-circular"
    assert walk.title == "Ashwell Circular via Fakeham"
    assert walk.published_distance_km == 9.87
    assert walk.published_duration_min == 165
    assert walk.toughness == 3
    assert walk.published_ascent_m == 190.0
    assert walk.region == "Kent"
    assert walk.summary.startswith("A gentle chalk walk")
    assert walk.start_crs == "ASW" and walk.finish_crs == "FKH"
    assert walk.london_departure_crs == ["BFR", "VIC"]
    assert walk.seeded_journey_minutes == 42 and walk.seeded_journey_source == "swc"

    kinds = [(v.kind, v.distance_km) for v in walk.variations]
    assert kinds == [
        (VariationKind.PUB_DETOUR, None),  # "adds about 1.5 km": no total stated
        (VariationKind.ALT_ENDING, 5.0),  # "At 5km : train back"
        (VariationKind.SHORTCUT, pytest.approx(9.07)),  # "cut 0.8km off"
    ]
    assert all(v.provenance == Provenance.HEURISTIC for v in walk.variations)

    lunch = [f for f in walk.food_stops if f.role == FoodRole.LUNCH]
    assert [f.name for f in lunch] == ["The Fox", "The Plough"]
    assert lunch[0].phone == "01234 567 890"
    assert "open from 12 noon Wednesday to Sunday" in lunch[0].hours_note
    assert lunch[1].position_km == 6.0
    assert "until 2.30pm" in lunch[1].hours_note
    tea = [f for f in walk.food_stops if f.role == FoodRole.TEA]
    assert tea[0].name == "Ashwell Tea Room" and tea[0].phone == "01234 567 892"
    assert walk.food_notes.startswith("Lunch: The suggested lunch stop")

    assert "Leave Ashwell station" in walk.directions
    assert "Detour to the Fox" in walk.directions
    assert "Donate" not in walk.directions


def test_parse_new_generation_page(swc_dir: Path, refs) -> None:
    walk = parse_walk_page((swc_dir / "walk_new.html").read_text(encoding="utf-8"), refs[1])
    assert walk.source_id == "swc.512"
    assert walk.published_distance_km == 13.25  # "13¼ km"
    assert walk.published_duration_min == 200
    assert walk.toughness == 4
    assert walk.start_crs == "BML" and walk.finish_crs == "CFD"
    assert walk.seeded_journey_minutes == 65

    labels = [(v.label, v.kind, v.distance_km, v.provenance) for v in walk.variations]
    assert labels[0] == (
        "Longer Walk, via Dunmere Park",
        VariationKind.EXTENSION,
        16.0,
        Provenance.STRUCTURED,
    )
    assert labels[1] == (
        "Short Walk, finishing at Frantham station",
        VariationKind.ALT_ENDING,
        11.0,
        Provenance.STRUCTURED,
    )
    assert labels[2][2] == 11.0 and labels[2][3] == Provenance.HEURISTIC  # Walk Notes row

    names = {f.name: f for f in walk.food_stops}
    assert set(names) == {"George Inn", "Abergaveny Arms", "Juliets"}, "Frantham is a place"
    assert names["George Inn"].phone == "01234-567893"
    assert names["George Inn"].hours_note == "closed Mon lunchtime"
    assert names["George Inn"].position_km == 8.25
    assert names["Juliets"].role == FoodRole.TEA
    assert "open Tue\u2013Fri & Sun to 4pm" in names["Juliets"].hours_note


def test_page_without_optional_sections_still_parses(refs) -> None:
    page = "<h1>Dunmere Loop Walk</h1><table></table>"
    walk = parse_walk_page(page, refs[2])
    assert walk.title == "Dunmere Loop"
    assert walk.published_distance_km == 21.0  # from the listing
    assert walk.toughness == 7
    assert walk.variations == [] and walk.food_stops == [] and walk.directions is None


@pytest.mark.parametrize(
    ("text", "published", "km"),
    [
        ("Short Walk, omitting Chaldon loop: 12 km (7.5 miles).", 16.5, 12.0),
        ("This makes for a walk of 8.7 kms to Alfriston.", 21.3, 8.7),
        ("You can shorten the walk by 1.5km by not heading into the village.", 14.3, 12.8),
        ("At 8km : it is also possible to get a train back to London.", 12.4, 8.0),
        ("The detour to the pub adds 2 km to the walk.", 12.4, None),
        ("Plumpton station is about 4 kilometres from the lunch pub.", 17.7, None),
    ],
)
def test_estimate_variation_km(text: str, published: float, km: float | None) -> None:
    assert estimate_variation_km(text, published) == (pytest.approx(km) if km else None)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("from Victoria to Otford. Journey time 35 minutes. Fast trains back", 35),
        ("on the Brighton line; journey time, just over an hour. Trains back", 65),
        ("The journey time to Biggleswade is about 45 minutes from central London.", 45),
        ("two on Sundays, journey time is between 30 and 34 minutes. All trains", 34),
        ("(journey time about 1 hour 5 minutes)", 65),
        ("with a journey time from 143 mins from London", 143),
        ("commit to a specific train for the return journey. Advance tickets", None),
        ("Journey times: 55 mins to Ashford", 55),
    ],
)
def test_parse_journey_minutes(text: str, minutes: int | None) -> None:
    from rambler.sources.swc import parse_journey_minutes

    assert parse_journey_minutes([text]) == minutes


def test_classify_variation() -> None:
    assert classify_variation("Short Walk, finishing at Frant station") == VariationKind.ALT_ENDING
    assert classify_variation("Longer Walk, via Dunorlan Park") == VariationKind.EXTENSION
    assert (
        classify_variation("the detour to the Rising Sun pub adds 2 km") == VariationKind.PUB_DETOUR
    )
    assert classify_variation("Alternative start from Nutfield") == VariationKind.ALT_START
    assert classify_variation("cut 0.8km off the end") == VariationKind.SHORTCUT


def test_parse_gpx_url(swc_dir: Path) -> None:
    url = parse_gpx_url(
        (swc_dir / "download.html").read_text(encoding="utf-8"),
        "https://www.walkingclub.org.uk/walk/ashwell-circular/",
    )
    assert url == (
        "https://www.walkingclub.org.uk/walk/ashwell-circular/"
        "Ashwell-Circular-via-Fakeham-SWC-Walk-1-7.gpx"
    )


def test_swc_source_end_to_end_offline(swc_dir: Path, tmp_path: Path) -> None:
    files = {
        "/walk/": "listing.html",
        "/walk/ashwell-circular/": "walk_old.html",
        "/walk/ashwell-circular/download-GPX-KML.html": "download.html",
        "/walk/ashwell-circular/Ashwell-Circular-via-Fakeham-SWC-Walk-1-7.gpx": "walk.gpx",
    }
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        name = files.get(request.url.path)
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, text=(swc_dir / name).read_text(encoding="utf-8"))

    client = get_client(
        Settings(data_dir=tmp_path), transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    src = SwcSource(client)
    assert isinstance(src, WalkSource)
    refs = src.iter_walk_refs()
    raw = src.fetch_walk(refs[0])
    assert raw.gpx_url.endswith("SWC-Walk-1-7.gpx")
    assert raw.gpx and "<rte>" in raw.gpx
    walk = src.parse_walk(raw)
    assert walk.gpx_url == raw.gpx_url
    assert walk.published_distance_km == 9.87
    assert all(h.startswith("/walk/") for h in hits)

    # second fetch is served from the on-disk cache: no new requests
    before = len(hits)
    src.fetch_walk(refs[0])
    assert len(hits) == before
    client.close()
