import pytest
from typer.testing import CliRunner

import rambler
from rambler.cli import app
from rambler.config import UserProfile

pytestmark = pytest.mark.unit


def test_package_imports() -> None:
    assert rambler.__version__


def test_example_profile_loads(example_profile_path) -> None:
    profile = UserProfile.load(example_profile_path)
    assert [s.crs for s in profile.home_stations] == ["HNH", "BRX"]
    assert profile.walk.max_distance_km == 13
    assert profile.station("HNH").access_minutes("VIC") == 12
    assert profile.station("HNH").prefers("CHX"), "a null time still means preferred"
    assert profile.station("HNH").access_minutes("CHX") is None


def test_cli_version_and_profile_show(example_profile_path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert rambler.__version__ in result.output

    result = runner.invoke(app, ["profile", "show", "-p", str(example_profile_path)])
    assert result.exit_code == 0, result.output
    assert "Herne Hill" in result.output
