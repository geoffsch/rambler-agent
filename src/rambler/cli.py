"""Command-line entry point (``rambler``). Thin: real logic lives in the library."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from rambler import __version__
from rambler.config import (
    DEFAULT_PROFILE_PATH,
    EXAMPLE_PROFILE_PATH,
    Settings,
    Station,
    UserProfile,
)
from rambler.db.store import WalkStore
from rambler.models import Walk
from rambler.travel import (
    AccessEstimate,
    crow_km_from_london,
    estimate_access,
    rank_key,
    within_travel_budget,
)

app = typer.Typer(no_args_is_help=True, help="Plan countryside walking day trips from London.")
profile_app = typer.Typer(no_args_is_help=True, help="Inspect the personal profile.")
ingest_app = typer.Typer(no_args_is_help=True, help="Build the local walk database.")
walks_app = typer.Typer(no_args_is_help=True, help="Query the local walk database (no agent).")
app.add_typer(profile_app, name="profile")
app.add_typer(ingest_app, name="ingest")
app.add_typer(walks_app, name="walks")

console = Console()


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(f"rambler {__version__}")


def resolve_profile_path(path: Path | None) -> Path:
    """Explicit path > settings/env > ./user_profile.yaml > committed example (with a warning)."""
    if path is not None:
        return path
    candidate = Settings().profile_path
    if candidate.exists():
        return candidate
    if candidate == DEFAULT_PROFILE_PATH and EXAMPLE_PROFILE_PATH.exists():
        typer.secho(
            f"{DEFAULT_PROFILE_PATH} not found; using {EXAMPLE_PROFILE_PATH}. "
            "Copy it and personalise.",
            err=True,
            fg=typer.colors.YELLOW,
        )
        return EXAMPLE_PROFILE_PATH
    raise typer.BadParameter(f"profile not found at {candidate}")


ProfileOpt = Annotated[
    Path | None, typer.Option("--profile", "-p", help="Path to user_profile.yaml")
]


@profile_app.command("show")
def profile_show(path: ProfileOpt = None) -> None:
    """Load and print the resolved user profile (proves config loading works)."""
    resolved = resolve_profile_path(path)
    profile = UserProfile.load(resolved)
    typer.echo(f"# profile: {resolved}")
    typer.echo(yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False))


# ----------------------------------------------------------------------------- ingest


@ingest_app.command("swc")
def ingest_swc(
    limit: Annotated[int, typer.Option(help="Only the first N walks (0 = all)")] = 0,
    force_refresh: Annotated[bool, typer.Option(help="Bypass the HTTP cache")] = False,
    no_backfill: Annotated[bool, typer.Option(help="Skip Open-Meteo elevation backfill")] = False,
) -> None:
    """Crawl the Saturday Walkers Club catalogue (politely, cache-first) into data/walks.db."""
    from rambler.http import get_client
    from rambler.sources.ingest import ingest
    from rambler.sources.swc import SwcSource

    settings = Settings()
    if not settings.contact:
        typer.secho("Set RAMBLER_CONTACT in .env before crawling (contactable User-Agent).",
                    err=True, fg=typer.colors.RED)  # fmt: skip
        raise typer.Exit(2)
    with get_client(settings) as client, WalkStore(settings.db_path) as store:
        report = ingest(
            SwcSource(client),
            store,
            gpx_dir=settings.gpx_dir,
            limit=limit or None,
            force_refresh=force_refresh,
            backfill=not no_backfill,
            client=client,
            progress=lambda i, n, slug: (
                console.print(f"[dim]{i}/{n}[/] {slug}") if i % 25 == 0 or i == n else None
            ),
        )
    typer.echo(report.summary())
    if report.total and not report.ingested:
        typer.secho("nothing was ingested - see the errors above", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    if report.distance_mismatch:
        typer.echo("distance outliers (slug, published, computed):")
        for slug, pub, comp in sorted(
            report.distance_mismatch, key=lambda t: -abs(t[2] / t[1] - 1)
        ):
            typer.echo(f"  {slug}: {pub} vs {comp}")


# ----------------------------------------------------------------------------- walks


@walks_app.command("stats")
def walks_stats() -> None:
    """Counts, coverage and a distance histogram for the local database."""
    settings = Settings()
    with WalkStore(settings.db_path) as store:
        s = store.stats()
    if s["total"] == 0:
        typer.echo("database is empty; run `rambler ingest swc`")
        raise typer.Exit(1)
    total = s["total"]

    def pct(n: int) -> str:
        return f"{n:>4}  ({100 * n / total:.0f}%)"

    typer.echo(f"walks: {total}  by source: {s['by_source']}")
    for key in (
        "with_gpx", "with_computed_distance", "with_elevation", "with_seeded_journey",
        "with_start_crs", "with_start_point", "with_variations", "with_variation_distance",
        "with_food_stops",
        "with_food_phone", "with_directions",
    ):  # fmt: skip
        typer.echo(f"  {key:<26} {pct(s[key])}")
    typer.echo("\ndistance histogram (km, 2 km buckets, computed where available):")
    for b, n in s["distance_histogram_2km"]:
        typer.echo(f"  {b:>3}-{b + 2:<3} {'#' * n} {n}")
    bad = s["distance_mismatch_over_10pct"]
    typer.echo(f"\npublished vs computed distance >10% apart: {len(bad)}")
    for slug, pub, comp in bad[:15]:
        typer.echo(f"  {slug}: published {pub} km, computed {comp} km")


@walks_app.command("find")
def walks_find(
    max_km: Annotated[float | None, typer.Option(help="Max distance (computed if known)")] = None,
    min_km: Annotated[float | None, typer.Option(help="Min distance")] = None,
    from_crs: Annotated[
        str | None,
        typer.Option("--from", help="Home station CRS from your profile, e.g. HNH"),
    ] = None,
    max_travel_min: Annotated[
        int | None,
        typer.Option(
            help="Max approximate travel minutes. With --from this is door-to-door "
            "(home to terminus, from your profile, plus the source's terminus-to-start "
            "time); walks with no known time are kept and shown as '?'."
        ),
    ] = None,
    only_preferred: Annotated[
        bool,
        typer.Option(
            "--only-preferred",
            help="With --from, hide walks leaving from termini your profile does not list",
        ),
    ] = False,
    max_toughness: Annotated[int | None, typer.Option(help="SWC toughness 1-10")] = None,
    region: Annotated[list[str] | None, typer.Option(help="Region name(s), e.g. Kent")] = None,
    text: Annotated[str | None, typer.Option(help="Keyword search over title/summary/tags")] = None,
    with_options: Annotated[
        bool, typer.Option(help="Only walks with documented variations")
    ] = False,
    limit: Annotated[int, typer.Option()] = 40,
    profile: ProfileOpt = None,
) -> None:
    """A dumb filter over the walk database: no agent, no model, no network.

    Without --from, --max-travel-min filters strictly on the source-seeded time. With
    --from, your profile's terminus access times are added to give an approximate
    door-to-door figure, and walks from unlisted termini are ranked last, not hidden.
    """
    settings = Settings()
    station = None
    if from_crs:
        prof = UserProfile.load(resolve_profile_path(profile))
        station = prof.station(from_crs)
        if station is None:
            raise typer.BadParameter(f"{from_crs} is not a home station in the profile")
    elif only_preferred:
        raise typer.BadParameter("--only-preferred needs --from")

    fetch_limit = limit if station is None else max(limit * 10, 200)
    with WalkStore(settings.db_path) as store:
        if store.count() == 0:
            typer.echo("database is empty; run `rambler ingest swc`")
            raise typer.Exit(1)
        walks = store.find_walks(
            max_km=max_km,
            min_km=min_km,
            max_toughness=max_toughness,
            regions=region,
            # Travel filtering moves into Python when a home station is given, so that
            # the door-to-door estimate and the "keep unknowns" rule apply.
            max_travel_min=None if station else max_travel_min,
            text=text,
            limit=fetch_limit * 3 if with_options else fetch_limit,
        )
    if with_options:
        walks = [w for w in walks if w.variations]

    rows: list[tuple[Walk, AccessEstimate | None]] = [(w, None) for w in walks]
    if station is not None:
        pairs = [(w, estimate_access(w, station)) for w in walks]
        if only_preferred:
            pairs = [(w, e) for w, e in pairs if e.preferred]
        pairs = [(w, e) for w, e in pairs if within_travel_budget(e, max_travel_min)]
        pairs.sort(key=lambda pair: rank_key(*pair))
        rows = list(pairs)
    rows = rows[:limit]

    if not rows:
        typer.echo("no walks match")
        raise typer.Exit(1)
    console.print(_walk_table(rows, station))
    if station is not None:
        listed = ", ".join(
            f"{crs}{f' {mins}m' if mins is not None else ''}"
            for crs, mins in station.termini.items()
        )
        console.print(
            f"[dim]from {station.name} ({station.crs}); your termini: {listed or 'none listed'}."
            " 'mins' is access + the source's approximate terminus-to-start time, never"
            " bookable; '~' marks a terminus your profile does not list, and where no"
            " time is known the crow-flies distance from London is shown instead.[/]"
        )


def _walk_table(rows: list[tuple[Walk, AccessEstimate | None]], station: Station | None) -> Table:
    t = Table(show_lines=False, pad_edge=False, expand=False)
    cols = ("km", "asc", "tough", "via", "mins", "region", "walk", "options", "food")
    for col in cols:
        t.add_column(col, justify="right" if col in ("km", "asc", "tough", "mins") else "left")
    for w, est in rows:
        km = w.distance_km
        if est is None:
            via = " ".join(w.london_departure_crs)
            mins = str(w.seeded_journey_minutes or "")
        elif est.preferred:
            via = f"[bold]{est.terminus}[/]"
            mins = est.describe()
        else:
            via = f"[dim]~{' '.join(w.london_departure_crs) or '?'}[/]"
            mins = f"[dim]{est.describe()}[/]"
        if est is not None and est.total_minutes is None:
            # no usable time: say how far away it is rather than leave a bare "?"
            crow = crow_km_from_london(w)
            if crow is not None:
                mins = f"[dim]{est.describe()} {crow:.0f}km[/]"
        shorter = [v for v in w.variations if v.distance_km and km and v.distance_km < km]
        options = (
            f"{len(w.variations)} ({'/'.join(f'{v.distance_km:g}' for v in shorter[:3])} km)"
            if shorter
            else (str(len(w.variations)) if w.variations else "")
        )
        food = ", ".join(f.name + (" ☎" if f.phone else "") for f in w.food_stops[:2])
        t.add_row(
            f"{km:.1f}" if km else "?",
            f"{w.computed_ascent_m or w.published_ascent_m or 0:.0f}",
            str(w.toughness or "?"),
            via,
            mins,
            (w.region or "")[:14],
            f"{w.title}  [dim]{w.slug}[/]",
            options,
            food[:40],
        )
    return t


if __name__ == "__main__":  # pragma: no cover
    app()
