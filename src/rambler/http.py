"""The one HTTP layer (plan.md principle 3).

Every outbound request in the project goes through :func:`get_client`, which returns an
``httpx.Client`` wired as::

    httpx.Client  ->  hishel CacheTransport (on-disk, cache-forever)
                  ->  PoliteTransport (per-host rate limit + retry)
                  ->  httpx.HTTPTransport (the network)

Ordering matters: the cache sits *above* the rate limiter, so a cache hit never sleeps
and never counts against a host's interval. Ingestion re-runs are therefore free and
idempotent over the raw cache under ``data/cache/http/``.

The rate limiter and retry loop take injectable ``clock``/``sleep`` callables so they can
be unit-tested with a fake clock and no network.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path

import hishel
import httpx

from rambler.config import Settings

Clock = Callable[[], float]
Sleep = Callable[[float], None]

#: Minimum seconds between requests to a host. Anything not listed gets ``DEFAULT_INTERVAL``.
DEFAULT_HOST_POLICIES: Mapping[str, float] = {
    "www.walkingclub.org.uk": 1.0,
    "walkingclub.org.uk": 1.0,
    "overpass-api.de": 2.0,
    "overpass.kumi.systems": 2.0,
    "api.ratings.food.gov.uk": 0.5,
    "api.open-meteo.com": 0.2,
    "archive-api.open-meteo.com": 0.2,
    "transportapi.com": 0.5,
    "api.transitous.org": 1.0,
}
DEFAULT_INTERVAL = 0.5

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class RateLimiter:
    """Per-host minimum-interval limiter using a monotonic clock.

    Not thread-safe by design: the project is single-process and ingestion is sequential.
    """

    def __init__(
        self,
        min_intervals: Mapping[str, float] | None = None,
        *,
        default_interval: float = DEFAULT_INTERVAL,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._intervals = dict(DEFAULT_HOST_POLICIES if min_intervals is None else min_intervals)
        self._default = default_interval
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}

    def interval_for(self, host: str) -> float:
        return self._intervals.get(host, self._default)

    def wait(self, host: str) -> float:
        """Block until a request to ``host`` is allowed; return the seconds slept."""
        now = self._clock()
        last = self._last_request.get(host)
        slept = 0.0
        if last is not None:
            due = last + self.interval_for(host)
            if due > now:
                slept = due - now
                self._sleep(slept)
                now = self._clock()
        self._last_request[host] = now
        return slept


class PoliteTransport(httpx.BaseTransport):
    """Rate-limits per host and retries transient failures with exponential backoff.

    Retries on connection/transport errors and on :data:`RETRY_STATUSES`. The limiter is
    consulted before *every* attempt, including retries.
    """

    def __init__(
        self,
        inner: httpx.BaseTransport,
        limiter: RateLimiter,
        *,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        attempt = 0
        while True:
            self._limiter.wait(host)
            try:
                response = self._inner.handle_request(request)
            except httpx.TransportError:
                if attempt >= self._max_retries:
                    raise
            else:
                if response.status_code not in RETRY_STATUSES or attempt >= self._max_retries:
                    return response
                response.close()
            self._sleep(self._backoff_base * 2**attempt)
            attempt += 1

    def close(self) -> None:
        self._inner.close()


def build_cache_transport(inner: httpx.BaseTransport, cache_dir: Path) -> httpx.BaseTransport:
    """Wrap ``inner`` in an on-disk, cache-forever hishel transport.

    ``force_cache`` ignores upstream ``Cache-Control`` so that pages with no caching
    headers (SWC, Overpass) are still stored; ingestion decides when to refresh by
    clearing the directory, not by honouring TTLs.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    storage = hishel.FileStorage(base_path=cache_dir, ttl=None)
    controller = hishel.Controller(
        cacheable_methods=["GET"], force_cache=True, allow_stale=True, always_revalidate=False
    )
    return hishel.CacheTransport(transport=inner, storage=storage, controller=controller)


def get_client(
    settings: Settings | None = None,
    *,
    cache_dir: Path | None = None,
    host_policies: Mapping[str, float] | None = None,
    cache: bool = True,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> httpx.Client:
    """Return the project's shared-configuration HTTP client.

    Args:
        settings: source of the User-Agent and cache location; defaults to the environment.
        cache_dir: override the on-disk cache directory (default ``data/cache/http``).
        host_policies: override per-host minimum intervals (seconds).
        cache: set ``False`` to bypass the on-disk cache (still rate-limited).
        transport: innermost transport, for tests (e.g. ``httpx.MockTransport``).
        clock, sleep: injectable for deterministic tests.
    """
    settings = settings or Settings()
    limiter = RateLimiter(host_policies, clock=clock, sleep=sleep)
    polite = PoliteTransport(transport or httpx.HTTPTransport(), limiter, sleep=sleep)
    final: httpx.BaseTransport = polite
    if cache:
        final = build_cache_transport(polite, cache_dir or settings.http_cache_dir)
    return httpx.Client(
        transport=final,
        headers={"User-Agent": settings.user_agent},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    )
