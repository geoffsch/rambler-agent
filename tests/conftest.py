from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def example_profile_path() -> Path:
    return REPO_ROOT / "user_profile.example.yaml"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
