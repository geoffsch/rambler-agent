"""Phase 0.5 feasibility probe: how many SWC walks survive the family filter?

THROWAWAY-GRADE. Deliberately crude (regex over cached HTML); the real parser is plan 02.
All fetches go through ``rambler.http`` so they are rate-limited (>=1 s/request to SWC),
carry the contactable User-Agent, and land in ``data/cache/http/`` for plan 02 to reuse.

Usage::

    uv run python scripts/probe_swc.py                # listing only: (a) exact, keyword sniff
    uv run python scripts/probe_swc.py --fetch-pages  # + walk pages for the >13 km candidates
                                                      #   in the chosen station groups

Two counts are reported (plan.md s3 Phase 0.5):
  (a) walks <= MAX_KM as published;
  (b) walks > MAX_KM whose documented options plausibly bring them <= MAX_KM.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from rambler.http import get_client

BASE = "https://www.walkingclub.org.uk"
LISTING_URL = f"{BASE}/walk/"
MAX_KM = 13.0
MIN_PLAUSIBLE_KM = 3.0  # a "length" below this is a delta or a detour, not a walk

# London departure station groups. Victoria and Blackfriars/London Bridge are the easy
# termini for a Herne Hill / Tulse Hill / Brixton household (problem_statement.md).
STATION_GROUPS: dict[str, str] = {
    "VIC": "Victoria",
    "BFR": "Blackfriars",
    "STP": "Blackfriars",  # Thameslink; BFR/STP listed together on SWC
    "LBG": "London Bridge",
    "CHX": "London Bridge",  # Southeastern via London Bridge
    "CST": "London Bridge",
    "WAE": "London Bridge",
    # Reachable from South London but not in the plan's three groups; broken out so
    # "other" is not misleading. ECR = East Croydon (Southern via Tulse Hill/Norwood Jn).
    "ECR": "East Croydon",
    "CLJ": "Clapham Jn/Waterloo",
    "WAT": "Clapham Jn/Waterloo",
}
CORE_GROUPS = ("Victoria", "Blackfriars", "London Bridge")
ALL_GROUPS = (*CORE_GROUPS, "East Croydon", "Clapham Jn/Waterloo", "other")

# Crude sniff for "this walk has documented shortening options" in the listing description.
OPTION_WORDS = re.compile(
    r"\b(short(?:er|cut|en)?|option|variation|alternative|early|return(?:ing)? (?:by|via|from)|"
    r"cut|bus back|train back)\b",
    re.I,
)

ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TD_DATA_TEXT_RE = re.compile(r'<td data-text="([^"]*)"')
SLUG_RE = re.compile(r'href="/walk/([^"/]+)/"')
TAG_RE = re.compile(r"<[^>]+>")

# Rows of the walk-page summary table that carry documented variations. Labels vary in
# case and whitespace across the site's page generations.
OPTION_ROW_RE = re.compile(
    r"<th>\s*(walk options|walk notes|length|shortcuts?|options)\s*</th>\s*<td[^>]*>(.*?)</td>",
    re.I | re.S,
)
KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:km|kms|kilomet)", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+|\s*<li>\s*|\s*<br\s*/?>\s*|\s*</?p>\s*", re.I)
NUM = r"(\d+(?:\.\d+)?)"
# Explicit, conservative patterns only. Each yields either a TOTAL walk length or a DELTA
# to subtract from the published length. Anything else is ignored (under-counting is the
# safe direction for a go/no-go gate).
TOTAL_PATTERNS = [
    # "Short Walk, omitting X: 14 km (8.7 miles)."  /  "Alternative Circular Walk, from Y: 10 km"
    re.compile(rf"^\s*(?:short|shorter|alternative)[^:]{{0,80}}:\s*{NUM}\s*kms?\b", re.I),
    # "making a total walk of 11.5km" / "a walk of 8.7 kms" / "total distance of 13.6 kms"
    re.compile(
        rf"\b(?:walk|distance|route|circuit|total)(?:\s+length)?\s+(?:of|to)\s+"
        rf"(?:about\s+|around\s+|just\s+|only\s+)?{NUM}\s*kms?\b",
        re.I,
    ),
    # "reducing the walk (to Oxted) to 14.2km"
    re.compile(
        rf"\breduc\w*\s+(?:the\s+)?(?:walk|distance|it)[^,.;]{{0,30}}?\bto\s+{NUM}\s*kms?\b", re.I
    ),
    # "Short Walks of around 10 km each" / "a shorter option of 8 km"
    re.compile(
        rf"\bshort(?:er)?\s+(?:walks?|options?|versions?|routes?)\s+of\s+(?:around\s+|about\s+)?{NUM}\s*kms?\b",
        re.I,
    ),
    # "Seaford to Exceat 6.2km" inside a "Shorter options:" list
    re.compile(rf"^\s*shorter options?:.*?\b{NUM}\s*kms?\b", re.I),
]
# "At 8km : ... train back"  -> the walk can end at ~8 km.
AT_KM_RE = re.compile(rf"^\s*at\s+{NUM}\s*kms?\b", re.I)
AT_KM_END_RE = re.compile(r"train|bus|station|back|return|end|short", re.I)
DELTA_PATTERNS = [
    # "shorten the walk by 1.5km" / "cut 1.2km off" / "reduces the distance by 3.9 kms"
    re.compile(
        rf"\b(?:shorten\w*|reduc\w*|cut\w*|sav\w*|knock\w*)\b[^,.;]{{0,40}}?\bby\s+{NUM}\s*kms?\b",
        re.I,
    ),
    re.compile(rf"\bcut\w*\s+{NUM}\s*kms?\s+off\b", re.I),
    # "which is 6 km shorter than the Main Walk"
    re.compile(rf"\b{NUM}\s*kms?\s+shorter\b", re.I),
]


def strip(s: str) -> list[str]:
    return html.unescape(TAG_RE.sub(" ", s)).split()


def text(s: str) -> str:
    return " ".join(strip(s))


@dataclass
class Walk:
    number: str
    slug: str
    title: str
    region: str
    km: float
    effort: int | None
    ascent_m: int | None
    description: str
    stations: list[str]
    option_keywords: bool = False
    has_options_row: bool = False
    option_lines: list[str] = field(default_factory=list)
    derived: list[tuple[float, str]] = field(default_factory=list)

    @property
    def group(self) -> str:
        groups = {STATION_GROUPS.get(s, "other") for s in self.stations}
        for g in ALL_GROUPS[:-1]:
            if g in groups:
                return g
        return "other"

    @property
    def short_enough(self) -> bool:
        return self.km <= MAX_KM

    @property
    def plausible_options(self) -> list[tuple[float, str]]:
        return [d for d in self.derived if MIN_PLAUSIBLE_KM <= d[0] <= MAX_KM]

    @property
    def shortenable(self) -> bool:
        """Published > MAX_KM but a documented option plausibly gets it to <= MAX_KM."""
        return not self.short_enough and bool(self.plausible_options)

    @property
    def best_option(self) -> tuple[float, str]:
        return min(self.plausible_options, key=lambda d: d[0])


def _int(s: str) -> int | None:
    t = text(s)
    return int(float(t)) if t.replace(".", "").isdigit() else None


def parse_listing(page: str) -> list[Walk]:
    walks: list[Walk] = []
    for row in ROW_RE.findall(page):
        tds = TD_RE.findall(row)
        if len(tds) < 9 or "/walk/" not in tds[3]:
            continue
        # data-text attributes are on the <td> tags, which TD_RE strips; re-find per cell.
        cells = TD_DATA_TEXT_RE.findall(row)
        try:
            km = float(text(tds[4]))
        except ValueError:
            continue
        slug = SLUG_RE.search(tds[3])
        number = next((c for c in cells if c.startswith("swc.")), "")
        stations = next((c for c in cells if re.fullmatch(r"(?:[A-Z]{3}\s*)+", c)), "")
        description = text(tds[7])
        walks.append(
            Walk(
                number=number.removeprefix("swc."),
                slug=slug.group(1) if slug else "",
                title=text(tds[3]),
                region=text(tds[0]),
                km=km,
                effort=_int(tds[5]),
                ascent_m=_int(tds[6]),
                description=description,
                stations=stations.split(),
                option_keywords=bool(OPTION_WORDS.search(description)),
            )
        )
    return walks


def option_sentences(page: str) -> list[str]:
    """Sentences from the variation-bearing summary rows (crude: HTML split on li/br/p/.)."""
    out: list[str] = []
    for _label, body in OPTION_ROW_RE.findall(page):
        for chunk in SENTENCE_SPLIT.split(body):
            t = text(chunk)
            if t and KM_RE.search(t):
                out.append(t)
    return out


def derive_lengths(published_km: float, sentences: list[str]) -> list[tuple[float, str]]:
    """Guess the length of each documented variation from its sentence.

    Only explicit phrasings count (see TOTAL_PATTERNS / DELTA_PATTERNS / AT_KM_RE). A sentence
    can yield several candidate totals ("8.7 kms to Alfriston, 12.7 kms to Exceat"); all are
    kept. Deltas are subtracted from the published length. Everything else is ignored, so
    the count errs on the low side.
    """
    found: list[tuple[float, str]] = []
    for s in sentences:
        at = AT_KM_RE.match(s)
        if at and AT_KM_END_RE.search(s):
            found.append((float(at.group(1)), s))
            continue
        matched = False
        for pat in TOTAL_PATTERNS:
            for m in pat.finditer(s):
                found.append((float(m.group(1)), s))
                matched = True
        if matched:
            continue
        for pat in DELTA_PATTERNS:
            for m in pat.finditer(s):
                delta = float(m.group(1))
                if delta < published_km:
                    found.append((published_km - delta, s))
    return found


def histogram(walks: list[Walk], width: float = 2.0) -> list[tuple[str, int]]:
    buckets: Counter[int] = Counter(int(w.km // width) for w in walks)
    return [
        (f"{b * width:>4.0f}-{(b + 1) * width:<4.0f}", buckets[b])
        for b in range(min(buckets), max(buckets) + 1)
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--fetch-pages", action="store_true", help="fetch walk pages for >MAX_KM candidates"
    )
    ap.add_argument(
        "--groups",
        default=",".join(CORE_GROUPS),
        help="comma-separated station groups whose >MAX_KM walks get their pages fetched",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="only count the pages that would be fetched"
    )
    ap.add_argument("--limit", type=int, default=0, help="cap on pages fetched (0 = no cap)")
    args = ap.parse_args(argv)
    groups_to_fetch = args.groups.split(",")

    with get_client() as client:
        r = client.get(LISTING_URL)
        r.raise_for_status()
        walks = parse_listing(r.text)
        print(f"listing rows parsed: {len(walks)}  (from_cache={r.extensions.get('from_cache')})")

        fetched: list[Walk] = []
        if args.fetch_pages:
            fetched = [w for w in walks if not w.short_enough and w.group in groups_to_fetch]
            if args.limit:
                fetched = fetched[: args.limit]
            print(f"fetching {len(fetched)} walk pages (rate-limited; cache-first)...")
            if args.dry_run:
                return 0
            for i, w in enumerate(fetched, 1):
                page = client.get(f"{BASE}/walk/{w.slug}/")
                if page.status_code != 200:
                    print(f"  ! {w.slug}: HTTP {page.status_code}", file=sys.stderr)
                    continue
                w.has_options_row = bool(OPTION_ROW_RE.search(page.text))
                w.option_lines = option_sentences(page.text)
                w.derived = derive_lengths(w.km, w.option_lines)
                if i % 50 == 0:
                    print(f"  {i}/{len(fetched)}")

    a = [w for w in walks if w.short_enough]
    kw = [w for w in walks if not w.short_enough and w.option_keywords]
    b = [w for w in walks if w.shortenable]

    print()
    print(f"total walks indexed:                       {len(walks)}")
    print(f"(a) <= {MAX_KM:.0f} km as published:                 {len(a)}")
    print(f"    > {MAX_KM:.0f} km with option keywords (sniff):   {len(kw)}")
    if args.fetch_pages:
        print(
            f"    pages fetched: {len(fetched)}; with a variation-bearing row: "
            f"{sum(w.has_options_row for w in fetched)}; with a km figure in it: "
            f"{sum(bool(w.option_lines) for w in fetched)}"
        )
        print(f"(b) > {MAX_KM:.0f} km, documented option <= {MAX_KM:.0f} km:   {len(b)}")
        print(f"(a)+(b):                                   {len(a) + len(b)}")

    print("\nDistance histogram (published km, 2 km buckets):")
    for label, count in histogram(walks):
        print(f"  {label} {'#' * count} {count}")

    print("\nBy London departure station group:")
    hdr = f"  {'group':<20}{'total':>6}{'(a)':>6}{'sniff':>7}"
    if args.fetch_pages:
        hdr += f"{'(b)':>6}{'a+b':>6}"
    print(hdr)
    for g in ALL_GROUPS:
        gw = [w for w in walks if w.group == g]
        n_a = sum(w.short_enough for w in gw)
        line = f"  {g:<20}{len(gw):>6}{n_a:>6}"
        line += f"{sum((not w.short_enough) and w.option_keywords for w in gw):>7}"
        if args.fetch_pages:
            n_b = sum(w.shortenable for w in gw)
            line += f"{n_b:>6}{n_a + n_b:>6}"
            if g not in groups_to_fetch:
                line += "   (pages not fetched)"
        print(line)

    raw: Counter[str] = Counter(s for w in walks for s in w.stations)
    print("\nRaw departure-station facet (a walk can list several):")
    print("  " + ", ".join(f"{k}={v}" for k, v in raw.most_common()))

    if args.fetch_pages and b:
        print(f"\n(b) walks with the sentence that triggered them ({len(b)}):")
        by_group: dict[str, list[Walk]] = defaultdict(list)
        for w in b:
            by_group[w.group].append(w)
        for g in ALL_GROUPS:
            for w in sorted(by_group.get(g, []), key=lambda w: w.km):
                shortest, why = w.best_option
                print(f"  [{g[:3]}] {w.km:>5.1f} km -> ~{shortest:>4.1f} km  {w.title}")
                print(f'        "{why[:150]}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
