"""Configuration: secrets/settings from ``.env`` and the personal ``user_profile.yaml``.

Two distinct things live here, deliberately kept apart:

* :class:`Settings` -- machine/deployment configuration and secrets, read from the
  environment (and a ``.env`` file). Never committed.
* :class:`UserProfile` -- who the walks are for: home stations, preferred lines,
  distance and travel limits, lunch preferences. Read from ``user_profile.yaml``
  (git-ignored; ``user_profile.example.yaml`` is committed). Personalisation is
  config, never code (plan.md principle 4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PROFILE_PATH = Path("user_profile.yaml")
EXAMPLE_PROFILE_PATH = Path("user_profile.example.yaml")

PROJECT_URL = "https://github.com/geoffsch/rambler-agent"


class Settings(BaseSettings):
    """Environment-backed settings. See ``.env.example`` for every variable."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="RAMBLER_", extra="ignore"
    )

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    transportapi_app_id: str | None = Field(default=None, validation_alias="TRANSPORTAPI_APP_ID")
    transportapi_app_key: SecretStr | None = Field(
        default=None, validation_alias="TRANSPORTAPI_APP_KEY"
    )

    data_dir: Path = Path("data")
    """Root for everything generated or ingested: walks.db, GPX store, HTTP cache."""

    http_user_agent: str = f"rambler-agent/0.1 (+{PROJECT_URL}; contact: set RAMBLER_CONTACT)"
    """Contactable User-Agent sent on every request (Overpass/SWC etiquette)."""

    contact: str | None = None
    """Contact e-mail placed into the User-Agent when set (RAMBLER_CONTACT)."""

    profile_path: Path = DEFAULT_PROFILE_PATH

    @property
    def user_agent(self) -> str:
        if self.contact:
            return f"rambler-agent/0.1 (+{PROJECT_URL}; contact: {self.contact})"
        return self.http_user_agent

    @property
    def http_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "http"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "walks.db"

    @property
    def gpx_dir(self) -> Path:
        return self.data_dir / "gpx"


class Station(BaseModel):
    """A home station, and roughly how long it takes to reach each London terminus.

    ``termini`` is a *preference with a cost*, never a permission list: walks leaving
    from an unlisted terminus are still found, just flagged and ranked last. A value of
    ``null`` means "I would use this terminus but do not know the time".
    """

    name: str
    crs: Annotated[str, Field(min_length=3, max_length=3, description="National Rail CRS code")]
    termini: dict[str, int | None] = Field(
        default_factory=dict,
        description="London terminus CRS -> approximate minutes from this station",
    )

    @field_validator("crs")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("termini", mode="before")
    @classmethod
    def _normalise_termini(cls, v: object) -> object:
        """Accept a bare list (``[VIC, BFR]``) as well as a mapping, and upper-case keys."""
        if isinstance(v, list):
            return {str(t).upper(): None for t in v}
        if isinstance(v, dict):
            return {str(k).upper(): val for k, val in v.items()}
        return v

    def access_minutes(self, terminus_crs: str) -> int | None:
        return self.termini.get(terminus_crs.upper())

    def prefers(self, terminus_crs: str) -> bool:
        return terminus_crs.upper() in self.termini


class WalkConstraints(BaseModel):
    max_distance_km: float = 13.0
    max_travel_minutes: int = 90
    min_distance_km: float = 0.0


class LunchPreferences(BaseModel):
    preferred: list[str] = Field(default_factory=lambda: ["pub"])
    """Ordered venue preferences, e.g. ["pub", "cafe"]."""
    position_along_route: tuple[float, float] = (0.55, 0.80)
    """Target fraction of the walk completed at the lunch stop (lo, hi)."""
    notes: list[str] = Field(default_factory=list)


class PaceProfile(BaseModel):
    """Inputs to duration estimates (see ``rambler.geo.timing``)."""

    pace_factor: float = Field(default=1.4, gt=0)
    """Multiplier on Naismith's rule; 1.0 is a fit adult, ~1.4 a family with young kids."""
    lunch_stop_minutes: int = Field(default=60, ge=0)


class UserProfile(BaseModel):
    """The household the trips are planned for. Loaded from YAML."""

    home_stations: list[Station] = Field(min_length=1)
    transport_notes: list[str] = Field(default_factory=list)
    """Free-text preferences the agent should respect (preferred operators, termini)."""
    walk: WalkConstraints = Field(default_factory=WalkConstraints)
    pace: PaceProfile = Field(default_factory=PaceProfile)
    lunch: LunchPreferences = Field(default_factory=LunchPreferences)
    kids_ages: list[int] = Field(default_factory=list)

    def station(self, crs: str) -> Station | None:
        crs = crs.upper()
        return next((s for s in self.home_stations if s.crs == crs), None)

    def termini_for(self, crs: str | None = None) -> list[str]:
        """Preferred termini for one home station, or across all of them when ``crs`` is None."""
        stations = [self.station(crs)] if crs else self.home_stations
        seen: dict[str, None] = {}
        for s in stations:
            if s:
                seen.update(dict.fromkeys(s.termini))
        return list(seen)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PROFILE_PATH) -> UserProfile:
        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.model_validate(raw)


def load_settings(**overrides: object) -> Settings:
    """Construct :class:`Settings`; keyword overrides win over the environment."""
    return Settings(**overrides)  # type: ignore[arg-type]
