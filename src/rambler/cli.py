"""Command-line entry point (``rambler``). Thin: real logic lives in the library."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from rambler import __version__
from rambler.config import DEFAULT_PROFILE_PATH, EXAMPLE_PROFILE_PATH, Settings, UserProfile
from rambler.db.store import WalkStore
from rambler.models import Walk

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
        "with_food_stops", "with_food_phone", "with_directions",
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
        int | None, typer.Option(help="Max source-quoted journey time (approximate; no network)")
    ] = None,
    max_toughness: Annotated[int | None, typer.Option(help="SWC toughness 1-10")] = None,
    region: Annotated[list[str] | None, typer.Option(help="Region name(s), e.g. Kent")] = None,
    text: Annotated[str | None, typer.Option(help="Keyword search over title/summary/tags")] = None,
    with_options: Annotated[
        bool, typer.Option(help="Only walks with documented variations")
    ] = False,
    limit: Annotated[int, typer.Option()] = 40,
    profile: ProfileOpt = None,
) -> None:
    """A dumb filter over the walk database: no agent, no model, no network."""
    settings = Settings()
    termini: list[str] | None = None
    if from_crs:
        prof = UserProfile.load(resolve_profile_path(profile))
        station = prof.station(from_crs)
        if station is None:
            raise typer.BadParameter(f"{from_crs} is not a home station in the profile")
        termini = station.termini
        if not termini:
            raise typer.BadParameter(f"{station.name} has no `termini` listed in the profile")
    with WalkStore(settings.db_path) as store:
        if store.count() == 0:
            typer.echo("database is empty; run `rambler ingest swc`")
            raise typer.Exit(1)
        walks = store.find_walks(
            max_km=max_km,
            min_km=min_km,
            max_toughness=max_toughness,
            regions=region,
            london_crs_in=termini,
            max_travel_min=max_travel_min,
            text=text,
            limit=limit * 3 if with_options else limit,
        )
    if with_options:
        walks = [w for w in walks if w.variations][:limit]
    if not walks:
        typer.echo("no walks match")
        raise typer.Exit(1)
    console.print(_walk_table(walks, termini))
    if termini:
        console.print(
            f"[dim]from {from_crs.upper()} via {', '.join(termini)}; journey minutes are the"
            " source's approximate terminus-to-start times, not bookable.[/]"
        )


def _walk_table(walks: list[Walk], termini: list[str] | None) -> Table:
    t = Table(show_lines=False, pad_edge=False, expand=False)
    for col in ("km", "asc", "tough", "London", "min", "region", "walk", "options", "food"):
        t.add_column(col, justify="right" if col in ("km", "asc", "tough", "min") else "left")
    for w in walks:
        km = w.distance_km
        london = " ".join(
            f"[bold]{c}[/]" if termini and c in termini else c for c in w.london_departure_crs
        )
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
            london,
            str(w.seeded_journey_minutes or ""),
            (w.region or "")[:14],
            f"{w.title}  [dim]{w.slug}[/]",
            options,
            food[:40],
        )
    return t


if __name__ == "__main__":  # pragma: no cover
    app()
