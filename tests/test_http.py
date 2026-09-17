from pathlib import Path

import httpx
import pytest

from rambler.config import Settings
from rambler.http import PoliteTransport, RateLimiter, get_client

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_requests_per_host() -> None:
    clock = FakeClock()
    limiter = RateLimiter({"a.example": 1.0, "b.example": 2.0}, clock=clock, sleep=clock.sleep)

    assert limiter.wait("a.example") == 0.0  # first request: no wait
    assert limiter.wait("b.example") == 0.0  # different host: independent
    assert limiter.wait("a.example") == pytest.approx(1.0)
    clock.now += 0.4
    assert limiter.wait("a.example") == pytest.approx(0.6)
    assert limiter.wait("b.example") == pytest.approx(2.0 - 1.0 - 0.4 - 0.6)
    clock.now += 10
    assert limiter.wait("a.example") == 0.0


def test_rate_limiter_default_interval_for_unknown_host() -> None:
    clock = FakeClock()
    limiter = RateLimiter({}, default_interval=0.25, clock=clock, sleep=clock.sleep)
    limiter.wait("x.example")
    assert limiter.wait("x.example") == pytest.approx(0.25)


def test_polite_transport_retries_transport_errors_with_backoff() -> None:
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text="ok")

    transport = PoliteTransport(
        httpx.MockTransport(handler),
        RateLimiter({}, default_interval=0, clock=clock, sleep=clock.sleep),
        max_retries=3,
        backoff_base=1.0,
        sleep=clock.sleep,
    )
    with httpx.Client(transport=transport) as client:
        assert client.get("https://x.example/").text == "ok"
    assert calls == 3
    assert clock.sleeps == [1.0, 2.0]


def test_polite_transport_retries_503_then_gives_up() -> None:
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    transport = PoliteTransport(
        httpx.MockTransport(handler),
        RateLimiter({}, default_interval=0, clock=clock, sleep=clock.sleep),
        max_retries=2,
        sleep=clock.sleep,
    )
    with httpx.Client(transport=transport) as client:
        assert client.get("https://x.example/").status_code == 503
    assert calls == 3
    assert clock.sleeps == [1.0, 2.0]


def test_get_client_caches_on_disk_and_sets_user_agent(tmp_path: Path) -> None:
    clock = FakeClock()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="<html>hi</html>")  # no Cache-Control at all

    settings = Settings(data_dir=tmp_path, contact="me@example.com")
    cache_dir = tmp_path / "cache"
    with get_client(
        settings,
        cache_dir=cache_dir,
        transport=httpx.MockTransport(handler),
        host_policies={"x.example": 5.0},
        clock=clock,
        sleep=clock.sleep,
    ) as client:
        r1 = client.get("https://x.example/page")
        r2 = client.get("https://x.example/page")

    assert r1.text == r2.text == "<html>hi</html>"
    assert len(seen) == 1, "second GET must be served from the on-disk cache"
    assert seen[0].headers["User-Agent"].startswith("rambler-agent/")
    assert "me@example.com" in seen[0].headers["User-Agent"]
    assert clock.sleeps == [], "a cache hit must not consume the host rate-limit interval"
    assert any(cache_dir.iterdir())
