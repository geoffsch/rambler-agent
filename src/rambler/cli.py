"""Command-line entry point (``rambler``). Thin: real logic lives in the library."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml

from rambler import __version__
from rambler.config import DEFAULT_PROFILE_PATH, EXAMPLE_PROFILE_PATH, Settings, UserProfile

app = typer.Typer(no_args_is_help=True, help="Plan countryside walking day trips from London.")
profile_app = typer.Typer(no_args_is_help=True, help="Inspect the personal profile.")
app.add_typer(profile_app, name="profile")


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


@profile_app.command("show")
def profile_show(
    path: Annotated[
        Path | None, typer.Option("--path", "-p", help="Path to user_profile.yaml")
    ] = None,
) -> None:
    """Load and print the resolved user profile (proves config loading works)."""
    resolved = resolve_profile_path(path)
    profile = UserProfile.load(resolved)
    typer.echo(f"# profile: {resolved}")
    typer.echo(yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False))


if __name__ == "__main__":  # pragma: no cover
    app()
