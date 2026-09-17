from pathlib import Path

import pytest
from typer.testing import CliRunner

from rambler.cli import app
from rambler.db.store import WalkStore
from rambler.sources.ingest import ingest
from tests.test_ingest import FixtureSource

pytestmark = pytest.mark.unit


def test_walks_find_cli_uses_profile_termini(
    fixtures_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("RAMBLER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "250")  # stop rich wrapping the table
    monkeypatch.delenv("RAMBLER_PROFILE_PATH", raising=False)
    with WalkStore(tmp_path / "walks.db") as store:
        ingest(FixtureSource(fixtures_dir / "swc"), store, gpx_dir=tmp_path / "gpx", backfill=False)
        store.upsert(
            store.get_walk("ashwell-circular").model_copy(update={"computed_distance_km": 9.9})
        )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "home_stations:\n  - {name: Herne Hill, crs: HNH, termini: [VIC, BFR]}\n"
        "  - {name: Nowhere, crs: NWH}\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    r = runner.invoke(app, ["walks", "find", "--max-km", "13", "--from", "HNH", "-p", str(profile)])
    assert r.exit_code == 0, r.output
    assert "ashwell-circular" in r.output
    assert "bramley-to-cranfold" not in r.output, "LBG/CHX are not HNH termini"
    assert "The Fox" in r.output

    r = runner.invoke(app, ["walks", "find", "--from", "NWH", "-p", str(profile)])
    assert r.exit_code != 0 and "termini" in r.output

    r = runner.invoke(app, ["walks", "find", "--max-travel-min", "50", "-p", str(profile)])
    assert r.exit_code == 0 and "ashwell-circular" in r.output and "bramley" not in r.output

    r = runner.invoke(app, ["walks", "stats"])
    assert r.exit_code == 0 and "walks: 2" in r.output
