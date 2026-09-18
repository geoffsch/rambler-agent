from pathlib import Path

import pytest
from typer.testing import CliRunner

from rambler.cli import app
from rambler.db.store import WalkStore
from rambler.sources.ingest import ingest
from tests.test_ingest import FixtureSource

pytestmark = pytest.mark.unit

PROFILE = """
home_stations:
  - {name: Herne Hill, crs: HNH, termini: {VIC: 12, BFR: 15}}
  - {name: Nowhere, crs: NWH}
"""


@pytest.fixture
def db(fixtures_dir: Path, tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("RAMBLER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "250")  # stop rich wrapping the table
    monkeypatch.delenv("RAMBLER_PROFILE_PATH", raising=False)
    with WalkStore(tmp_path / "walks.db") as store:
        ingest(FixtureSource(fixtures_dir / "swc"), store, gpx_dir=tmp_path / "gpx", backfill=False)
        # ashwell: VIC/BFR, seeded 42 min. bramley: LBG/CHX, seeded 65 min.
        store.upsert(
            store.get_walk("ashwell-circular").model_copy(update={"computed_distance_km": 9.9})
        )
        store.upsert(
            store.get_walk("bramley-to-cranfold").model_copy(update={"computed_distance_km": 12.0})
        )
    return tmp_path


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile.yaml"
    path.write_text(PROFILE, encoding="utf-8")
    return path


def run(*args: str) -> str:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_unlisted_termini_are_ranked_last_not_hidden(db: Path, profile: Path) -> None:
    out = run("walks", "find", "--max-km", "13", "--from", "HNH", "-p", str(profile))
    assert "ashwell-circular" in out
    assert "bramley-to-cranfold" in out, "LBG/CHX are unlisted, but must still be offered"
    assert "~" in out, "the unlisted terminus is flagged"
    assert out.index("ashwell-circular") < out.index("bramley"), "preferred terminus ranks first"
    assert "12+42=54" in out, "door-to-door = profile access + seeded onward time"


def test_only_preferred_restores_the_hard_filter(db: Path, profile: Path) -> None:
    out = run("walks", "find", "--from", "HNH", "--only-preferred", "-p", str(profile))
    assert "ashwell-circular" in out and "bramley-to-cranfold" not in out


def test_travel_budget_uses_door_to_door_and_keeps_unknowns(db: Path, profile: Path) -> None:
    out = run("walks", "find", "--from", "HNH", "--max-travel-min", "50", "-p", str(profile))
    assert "ashwell-circular" not in out, "12 + 42 = 54 minutes exceeds the budget"

    out = run("walks", "find", "--from", "HNH", "--max-travel-min", "60", "-p", str(profile))
    assert "ashwell-circular" in out
    assert "bramley-to-cranfold" in out, "unknown access time is kept, not silently dropped"


def test_without_from_the_seeded_filter_is_strict(db: Path) -> None:
    out = run("walks", "find", "--max-travel-min", "50")
    assert "ashwell-circular" in out and "bramley-to-cranfold" not in out


def test_errors(db: Path, profile: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(app, ["walks", "find", "--from", "ZZZ", "-p", str(profile)])
    assert r.exit_code != 0 and "not a home station" in r.output

    r = runner.invoke(app, ["walks", "find", "--only-preferred", "-p", str(profile)])
    assert r.exit_code != 0 and "needs --from" in r.output

    # a home station with no termini listed is usable: everything is simply unlisted
    r = runner.invoke(app, ["walks", "find", "--from", "NWH", "-p", str(profile)])
    assert r.exit_code == 0 and "ashwell-circular" in r.output


def test_stats(db: Path) -> None:
    assert "walks: 2" in run("walks", "stats")
