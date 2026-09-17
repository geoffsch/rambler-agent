from pathlib import Path

import pytest

from rambler.config import Settings, UserProfile

pytestmark = pytest.mark.unit


def test_profile_defaults_and_crs_upper(tmp_path: Path) -> None:
    p = tmp_path / "profile.yaml"
    p.write_text("home_stations:\n  - {name: Herne Hill, crs: hnh}\n", encoding="utf-8")
    profile = UserProfile.load(p)
    assert profile.home_stations[0].crs == "HNH"
    assert profile.walk.max_distance_km == 13.0
    assert profile.walk.max_travel_minutes == 90
    assert profile.lunch.preferred == ["pub"]
    assert profile.kids_ages == []


def test_profile_requires_home_station(tmp_path: Path) -> None:
    p = tmp_path / "profile.yaml"
    p.write_text("home_stations: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        UserProfile.load(p)


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("RAMBLER_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("RAMBLER_CONTACT", "me@example.com")
    s = Settings()
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(s)
    assert s.http_cache_dir == tmp_path / "d" / "cache" / "http"
    assert "me@example.com" in s.user_agent
    assert "github.com" in s.user_agent
