"""Saturday Walkers Club (walkingclub.org.uk) as a :class:`WalkSource`.

Personal-use only (see README). Everything is fetched through :func:`rambler.http.get_client`
so requests are rate-limited (1/s), carry a contactable User-Agent and are cached on disk;
re-running ingestion makes no requests unless ``force_refresh`` is set.

Page anatomy (both site generations; the newer one uses upper-case tags):

* ``/walk/`` -- one HTML table of every walk: region, tags, SWC id, title, km, effort,
  height gain, one-line description, London departure stations as CRS codes.
* ``/walk/<slug>/`` -- a summary ``<table>`` of ``<th>Label</th><td>...</td>`` rows
  (Length, Toughness, OS Map, Features, Walk Options / Walk notes, Travel, By Train,
  Lunch, Tea ...) followed by ``<h2>Walk Directions</h2>``.
* ``/walk/<slug>/download-GPX-KML.html`` -- links to one GPX whose first ``<rte>`` is
  the main walk and later routes are documented options.

Hard fields (distance, toughness, stations, GPX) come from structured elements and are
marked ``structured``; variations and food stops are regex over prose and are marked
``heuristic`` so downstream code can treat them with appropriate suspicion.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx

from rambler.http import get_client
from rambler.models import FoodRole, FoodStop, Provenance, Variation, VariationKind, Walk
from rambler.sources.base import RawWalk, WalkRef

SOURCE_ID = "swc"
BASE_URL = "https://www.walkingclub.org.uk"
LISTING_URL = f"{BASE_URL}/walk/"

# ----------------------------------------------------------------------------- text utils

_TAG = re.compile(r"<[^>]+>")
_FRACTIONS = {"¼": ".25", "½": ".5", "¾": ".75", "⅓": ".33", "⅔": ".67"}
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "half": 0.5,
}  # fmt: skip


def text_of(fragment: str) -> str:
    """Strip tags, unescape entities, normalise fractions and whitespace."""
    s = html.unescape(_TAG.sub(" ", fragment))
    for frac, dec in _FRACTIONS.items():
        s = re.sub(rf"(\d)\s*{frac}", rf"\1{dec}", s)  # "13¼" -> "13.25"
        s = s.replace(frac, "0" + dec)  # a bare "½" -> "0.5"
    return " ".join(s.replace("\u202f", " ").replace("\xa0", " ").split())


def paragraphs_of(fragment: str) -> list[str]:
    """Split an HTML fragment into text items on <p>, <li> and <br> boundaries."""
    parts = re.split(r"(?i)<\s*(?:p|li|br\s*/?)\b[^>]*>", fragment)
    return [t for t in (text_of(p) for p in parts) if t]


_KM = r"(\d+(?:\.\d+)?)\s*(?:km|kms|kilomet\w*)\b"
_KM_RE = re.compile(_KM, re.I)
_HOURS_WORD = r"(?:\d+|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_DURATION_RE = re.compile(
    # "1 hour 5 minutes" / "Three hours 20 minutes" / "an hour and a half" / "2 hrs"
    rf"(?P<h>{_HOURS_WORD})\s*(?:and a (?:half|quarter)\s*)?(?:hours?|hrs?)"
    r"(?:\s*and\s*a\s*(?P<frac>half|quarter))?(?:\s*(?:and\s*)?(?P<hm>\d+)\s*(?:minutes?|mins?))?"
    # "30 and 34 minutes" / "19 to 26 mins" / "40-50 minutes" / "45 minutes"
    r"|(?:(?P<lo>\d+)\s*(?:-|\u2013|to|and)\s*)?(?P<m>\d+)\s*(?:minutes?|mins?)"
    r"|(?P<halfhour>half an hour)",
    re.I,
)
_HOUR_QUALIFIER_RE = re.compile(r"\b(just over|just under|under|over)\s+(?:an|one)\s+hour", re.I)


def parse_minutes(text: str) -> int | None:
    """First duration phrase in ``text`` as minutes.

    Handles "3 hours 30 minutes", "Three hours", "an hour and a half", "half an hour",
    "just over/under an hour" (+/- 5) and ranges ("30 and 34 minutes", "19 to 26 mins"),
    for which the **upper** bound is returned (conservative for travel-time filters).
    """
    m = _DURATION_RE.search(text)
    if not m:
        return None
    if m.group("halfhour"):
        return 30
    if m.group("m"):
        return int(m.group("m"))
    hours_raw = m.group("h").lower()
    hours = 1.0 if hours_raw == "an" else float(_WORD_NUMBERS.get(hours_raw, hours_raw))
    whole = m.group(0).lower()
    if "and a half" in whole:
        hours += 0.5
    elif "and a quarter" in whole:
        hours += 0.25
    minutes = round(hours * 60 + int(m.group("hm") or 0))
    q = _HOUR_QUALIFIER_RE.search(text[max(0, m.start() - 12) : m.end()])
    if q:
        minutes += 5 if "over" in q.group(1).lower() else -5
    return minutes


# ----------------------------------------------------------------------------- listing

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)
_TD_DATA_TEXT_RE = re.compile(r'<td\s+data-text="([^"]*)"', re.I)
_SLUG_RE = re.compile(r'href="/walk/([^"/]+)/"')


def parse_listing(page: str) -> list[WalkRef]:
    """Rows of the ``/walk/`` table as :class:`WalkRef` with listing metadata attached."""
    refs: list[WalkRef] = []
    for row in _ROW_RE.findall(page):
        tds = _TD_RE.findall(row)
        if len(tds) < 9 or "/walk/" not in tds[3]:
            continue
        slug_m = _SLUG_RE.search(tds[3])
        if not slug_m:
            continue
        data = _TD_DATA_TEXT_RE.findall(row)
        source_id = next((d for d in data if re.fullmatch(r"[a-z]+\d*\.\d+", d)), "")
        stations = next((d for d in data if re.fullmatch(r"(?:[A-Z]{3}\s*)+", d)), "")
        tags_raw = next((d for d in data if re.match(r"^\d\s", d)), "")
        tags = [t.strip() for t in tags_raw[1:].split(",") if t.strip()]
        km = _to_float(text_of(tds[4]))
        slug = slug_m.group(1)
        refs.append(
            WalkRef(
                source=SOURCE_ID,
                source_id=source_id or slug,
                slug=slug,
                url=f"{BASE_URL}/walk/{slug}/",
                listing={
                    "title": text_of(tds[3]),
                    "region": text_of(tds[0]) or None,
                    "tags": tags,
                    "km": km,
                    "effort": _to_int(text_of(tds[5])),
                    "ascent_m": _to_float(text_of(tds[6])),
                    "description": text_of(tds[7]) or None,
                    "london_departure_crs": stations.split(),
                },
            )
        )
    return refs


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(s: str) -> int | None:
    f = _to_float(s)
    return int(f) if f is not None else None


# ----------------------------------------------------------------------------- walk page

_ROW_BLOCK_RE = re.compile(
    r"<th[^>]*>\s*(.*?)\s*</th>\s*<td[^>]*>(.*?)(?=<tr\b|</table>)", re.I | re.S
)
_WALK_ID_RE = re.compile(r'data-walk_id="([^"]+)"')
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_DIRECTIONS_RE = re.compile(
    r'id="swc-book-walk-instructions-title"[^>]*>.*?</h2>(.*?)(?=<h4>\s*Donate|<div[^>]*swc-donate|$)',
    re.I | re.S,
)
_TRAIN_OUT_RE = re.compile(r'data-to="([A-Z]{3})"[^>]*data-from="London"', re.I)
_TRAIN_BACK_RE = re.compile(r'data-from="([A-Z]{3})"[^>]*data-to="London"', re.I)
# "journey time" then, within the same clause, the first duration phrase. The lazy gap
# absorbs "to Biggleswade is about", ", just over", "of", ":", "from" and similar.
_JOURNEY_RE = re.compile(
    r"journey\s+times?\b(?P<gap>[^.;]{0,50}?)"
    r"(?P<dur>(?:\d+\s*(?:-|\u2013|to|and)\s*)?\d+\s*(?:hours?|hrs?|minutes?|mins?)"
    r"(?:\s*(?:and\s*)?\d+\s*(?:minutes?|mins?))?"
    r"|(?:an|one|two|three)\s+hours?(?:\s+and\s+a\s+(?:half|quarter))?|half an hour)",
    re.I,
)
_TOUGHNESS_RE = re.compile(r"(\d{1,2})\s*(?:out of|/)\s*10", re.I)
_GPX_HREF_RE = re.compile(r'href="([^"]+\.gpx)"', re.I)
_PHONE_RE = re.compile(
    r"\(?(?:tel\.?|telephone|phone|t:)?\s*(0\d{2,4}[\s\-]?\d{3}[\s\-]?\d{3,4}|0\d{2,4}[\s\-]\d{6,7}|0\d{9,10})\b",
    re.I,
)
_HOURS_RE = re.compile(
    r"\b(open(?:s|ing)?\b|closed\b|closes\b|last orders|serv(?:es|ing) (?:food|lunch|meals)|"
    r"(?:food|lunch) (?:until|till|to|from)|(?:until|till|to) \d{1,2}(?:[.:]\d{2})?\s*(?:am|pm))",
    re.I,
)
_NAME_RE = re.compile(r"<(?:b|strong)\b[^>]*>(.*?)</(?:b|strong)>", re.I | re.S)
_POSITION_RE = re.compile(
    rf"(?:after|at|about|approx\.?|around)\s+{_KM}|{_KM}\s+(?:into|from the start of|along)", re.I
)


def summary_rows(page: str) -> dict[str, list[str]]:
    """``{label_lower: [row_html, ...]}`` for the summary table (labels can repeat)."""
    rows: dict[str, list[str]] = {}
    for label, body in _ROW_BLOCK_RE.findall(page):
        key = text_of(label).lower()
        if key:
            rows.setdefault(key, []).append(body)
    return rows


def _first(rows: dict[str, list[str]], *labels: str) -> str | None:
    for label in labels:
        if rows.get(label):
            return rows[label][0]
    return None


def _all(rows: dict[str, list[str]], *labels: str) -> list[str]:
    return [body for label in labels for body in rows.get(label, [])]


def parse_length(row_html: str) -> tuple[float | None, int | None, list[Variation]]:
    """Distance, walking minutes, and named variants from a Length row.

    Old pages: "12.38km (7.69 miles), 3 hours 30 minutes. ..." New pages list several
    bold-labelled variants: "**Short Walk, finishing at Frant station:** 11 km ...".
    """
    items = paragraphs_of(row_html)
    if not items:
        return None, None, []
    distance = duration = None
    variants: list[Variation] = []
    for i, item in enumerate(items):
        km_m = _KM_RE.search(item)
        km = float(km_m.group(1)) if km_m else None
        mins = parse_minutes(item[km_m.end() :]) if km_m else parse_minutes(item)
        label_m = re.match(r"^(.{3,80}?):\s", item)
        is_main = i == 0 and (not label_m or label_m.group(1).lower().startswith("main"))
        if is_main:
            distance, duration = km, mins
        elif km:
            label = label_m.group(1) if label_m else item[:60]
            variants.append(
                Variation(
                    label=label,
                    kind=classify_variation(label),
                    distance_km=km,
                    note=item,
                    provenance=Provenance.STRUCTURED,
                )
            )
    return distance, duration, variants


def classify_variation(text: str) -> VariationKind:
    t = text.lower()
    if re.search(r"\b(pub|inn|arms|detour to the)\b", t) and "detour" in t:
        return VariationKind.PUB_DETOUR
    if re.search(
        r"\b(finish(?:ing)?|ending|end(?:s)? at|train back|bus back|"
        r"return(?:ing)? (?:by|from|via))\b",
        t,
    ):
        return VariationKind.ALT_ENDING
    if re.search(r"\b(start(?:ing)? (?:from|at)|alternative start)\b", t):
        return VariationKind.ALT_START
    if re.search(r"\b(short(?:er|cut|en)?|cut(?:s|ting)?|omit(?:s|ting)?|sav(?:e|es|ing))\b", t):
        return VariationKind.SHORTCUT
    if re.search(r"\b(longer|extension|extend(?:s|ed|ing)?|add(?:s|ing)?)\b", t):
        return VariationKind.EXTENSION
    return VariationKind.OTHER


_NUM = r"(\d+(?:\.\d+)?)"
_TOTAL_PATTERNS = [
    re.compile(rf"^\s*(?:short|shorter|alternative|longer)[^:]{{0,80}}:\s*{_NUM}\s*kms?\b", re.I),
    re.compile(
        rf"\b(?:walk|distance|route|circuit|total)(?:\s+length)?\s+(?:of|to)\s+"
        rf"(?:about\s+|around\s+|just\s+|only\s+)?{_NUM}\s*kms?\b",
        re.I,
    ),
    re.compile(
        rf"\breduc\w*\s+(?:the\s+)?(?:walk|distance|it)[^,.;]{{0,30}}?\bto\s+{_NUM}\s*kms?\b", re.I
    ),
    re.compile(
        rf"\bshort(?:er)?\s+(?:walks?|options?|versions?|routes?)\s+of\s+(?:around\s+|about\s+)?{_NUM}\s*kms?\b",
        re.I,
    ),
]
_AT_KM_RE = re.compile(rf"^\s*at\s+{_NUM}\s*kms?\b", re.I)
_AT_KM_END_RE = re.compile(r"train|bus|station|back|return|end|short", re.I)
_DELTA_PATTERNS = [
    re.compile(
        rf"\b(?:shorten\w*|reduc\w*|cut\w*|sav\w*|knock\w*)\b[^,.;]{{0,40}}?\bby\s+{_NUM}\s*kms?\b",
        re.I,
    ),
    re.compile(rf"\bcut\w*\s+{_NUM}\s*kms?\s+off\b", re.I),
    re.compile(rf"\b{_NUM}\s*kms?\s+shorter\b", re.I),
]


def estimate_variation_km(text: str, published_km: float | None) -> float | None:
    """Total length implied by an option sentence, using only explicit phrasings; else None."""
    for pat in _TOTAL_PATTERNS:
        m = pat.search(text)
        if m:
            return float(m.group(1))
    if published_km:
        for pat in _DELTA_PATTERNS:
            m = pat.search(text)
            if m and float(m.group(1)) < published_km:
                return round(published_km - float(m.group(1)), 2)
    at = _AT_KM_RE.match(text)
    if at and _AT_KM_END_RE.search(text):
        return float(at.group(1))
    return None


def parse_options(row_htmls: Iterable[str], published_km: float | None) -> list[Variation]:
    """Variations from "Walk Options" / "Walk notes" rows: one per list item or paragraph."""
    out: list[Variation] = []
    for body in row_htmls:
        for item in paragraphs_of(body):
            if len(item) < 15:
                continue
            label_m = re.match(r"^(.{3,80}?)\s*:\s", item)
            label = label_m.group(1) if label_m else item[:70].rsplit(" ", 1)[0]
            km = estimate_variation_km(item, published_km)
            out.append(
                Variation(
                    label=label,
                    kind=classify_variation(item),
                    distance_km=km,
                    note=item,
                    provenance=Provenance.HEURISTIC,
                )
            )
    return out


def parse_food(row_html: str, role: FoodRole) -> list[FoodStop]:
    """Food stops from a Lunch/Tea row: each bold name plus the sentence that follows it."""
    stops: list[FoodStop] = []
    seen: set[str] = set()
    for m in _NAME_RE.finditer(row_html):
        name = text_of(m.group(1)).strip(" :,.")
        if not name or name.lower() in seen or len(name) > 60 or name.lower().endswith(":"):
            continue
        # skip bold words that are places/sections rather than venues: keep venues with a
        # phone, a venue-ish word, or a following parenthetical
        after_html = row_html[m.end() : m.end() + 600]
        after = text_of(after_html)
        sentence = re.split(r"(?<=[.!?])\s+(?=[A-Z])", after, maxsplit=1)[0]
        phone_m = _PHONE_RE.search(sentence)
        venue_word = re.search(
            r"\b(pub|inn|arms|tavern|cafe|café|tea ?rooms?|restaurant|hotel|bar|kitchen|bakery|"
            r"farm shop|deli|coffee|brasserie|bistro|head|crown|bell|swan|lion|horse|oak)\b",
            name.lower(),
        )
        # A bold word with neither a phone nor a venue-ish name is a place, not a venue.
        if not (phone_m or venue_word):
            continue
        seen.add(name.lower())
        hours = None
        for clause in re.split(r"[;()]|\.\s", sentence):  # keep "2.30pm" intact
            if _HOURS_RE.search(clause):
                hours = clause.strip(" ,")
                break
        # position along the walk: the distance phrase nearest the name, looking back first
        before = text_of(row_html[max(0, m.start() - 120) : m.start()])
        candidates = [*_POSITION_RE.finditer(before)][-1:] or [
            *_POSITION_RE.finditer(sentence[:60])
        ][:1]
        pos_m = candidates[0] if candidates else None
        position = float(pos_m.group(1) or pos_m.group(2)) if pos_m else None
        stops.append(
            FoodStop(
                name=name,
                role=role,
                note=(name + " " + sentence).strip()[:500],
                phone=phone_m.group(1).strip() if phone_m else None,
                hours_note=hours,
                position_km=position,
            )
        )
    return stops


def parse_journey_minutes(texts: Iterable[str]) -> int | None:
    """First "journey time ..." duration across the given texts, in minutes."""
    for t in texts:
        m = _JOURNEY_RE.search(t)
        if m:
            mins = parse_minutes(m.group("gap") + m.group("dur"))
            if mins:
                return mins
    return None


def parse_walk_page(page: str, ref: WalkRef) -> Walk:
    """Structured + heuristic extraction from one walk page (no network)."""
    rows = summary_rows(page)
    listing = ref.listing
    title_m = _H1_RE.search(page)
    title = text_of(title_m.group(1)) if title_m else listing.get("title") or ref.slug
    title = re.sub(r"\s+Walk$", "", title)
    id_m = _WALK_ID_RE.search(page)

    length_row = _first(rows, "length")
    distance, duration, length_variants = (
        parse_length(length_row) if length_row else (None, None, [])
    )
    distance = distance or listing.get("km")

    toughness = None
    tough_row = _first(rows, "toughness")
    if tough_row and (tm := _TOUGHNESS_RE.search(text_of(tough_row))):
        toughness = int(tm.group(1))
    toughness = toughness or listing.get("effort")

    start_crs = finish_crs = None
    if train_row := _first(rows, "by train"):
        if om := _TRAIN_OUT_RE.search(train_row):
            start_crs = om.group(1).upper()
        if bm := _TRAIN_BACK_RE.search(train_row):
            finish_crs = bm.group(1).upper()

    travel_texts = [
        text_of(b)
        for b in _all(
            rows,
            "travel",
            "transport",
            "by train",
            "getting there",
            "suggested train",
            "train times",
        )
    ]
    journey = parse_journey_minutes(travel_texts)

    features = _first(rows, "features")
    summary = (paragraphs_of(features) or [None])[0] if features else None
    summary = summary or listing.get("description")

    variations = length_variants + parse_options(
        _all(rows, "walk options", "walk notes", "options", "shortcuts", "shortcut"), distance
    )
    food = [
        *[s for b in _all(rows, "lunch", "lunch and tea") for s in parse_food(b, FoodRole.LUNCH)],
        *[s for b in _all(rows, "tea") for s in parse_food(b, FoodRole.TEA)],
    ]
    food_notes = (
        "\n\n".join(
            f"{label.title()}: {text_of(b)}"
            for label in ("lunch", "lunch and tea", "tea")
            for b in rows.get(label, [])
        )
        or None
    )

    dir_m = _DIRECTIONS_RE.search(page)
    directions = "\n".join(paragraphs_of(dir_m.group(1))) if dir_m else None

    return Walk(
        source=SOURCE_ID,
        source_id=(id_m.group(1) if id_m else ref.source_id),
        slug=ref.slug,
        title=title,
        url=ref.url,
        region=listing.get("region"),
        summary=summary,
        published_distance_km=distance,
        published_ascent_m=listing.get("ascent_m"),
        published_duration_min=duration,
        toughness=toughness,
        start_crs=start_crs,
        finish_crs=finish_crs,
        london_departure_crs=list(listing.get("london_departure_crs", [])),
        seeded_journey_minutes=journey,
        seeded_journey_source=SOURCE_ID if journey else None,
        variations=variations,
        food_stops=food,
        food_notes=food_notes,
        directions=directions,
        tags=list(listing.get("tags", [])),
        fetched_at=datetime.now(UTC),
    )


def parse_gpx_url(download_page: str, walk_url: str) -> str | None:
    """The main GPX link on the download page (skips the ``auto/…-trk.gpx`` track-only copy)."""
    hrefs = _GPX_HREF_RE.findall(download_page)
    main = [h for h in hrefs if "/auto/" not in h and not h.startswith("auto/")]
    return urljoin(walk_url, (main or hrefs)[0]) if hrefs else None


# ----------------------------------------------------------------------------- source class


class SwcSource:
    """The Saturday Walkers Club catalogue behind the :class:`WalkSource` protocol."""

    source_id = SOURCE_ID

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = get_client()
        return self._client

    def _get(self, url: str, *, force_refresh: bool = False) -> str:
        ext = {"cache_disabled": True} if force_refresh else {}
        r = self.client.get(url, extensions=ext)
        r.raise_for_status()
        return r.text

    def iter_walk_refs(self, *, force_refresh: bool = False) -> list[WalkRef]:
        return parse_listing(self._get(LISTING_URL, force_refresh=force_refresh))

    def fetch_walk(self, ref: WalkRef, *, force_refresh: bool = False) -> RawWalk:
        page = self._get(ref.url, force_refresh=force_refresh)
        raw = RawWalk(ref=ref, page=page)
        if "download-GPX-KML.html" in page:
            dl = self._get(urljoin(ref.url, "download-GPX-KML.html"), force_refresh=force_refresh)
            raw.gpx_url = parse_gpx_url(dl, ref.url)
            if raw.gpx_url:
                raw.gpx = self._get(raw.gpx_url, force_refresh=force_refresh)
        return raw

    def parse_walk(self, raw: RawWalk) -> Walk:
        if raw.page is None:
            raise ValueError(f"{raw.ref.slug}: no page fetched")
        walk = parse_walk_page(raw.page, raw.ref)
        walk.gpx_url = raw.gpx_url
        return walk
