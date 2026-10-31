"""Benchmarks against a local Redis.

    python benchmarks/run.py race       # correctness under contention vs DRF's built-in throttle
    python benchmarks/run.py latency    # per-check latency for each algorithm

REDIS_URL defaults to redis://localhost:6379/14. The database is flushed.
"""

from __future__ import annotations

import os
import statistics
import sys
import threading
import time
from pathlib import Path

import django
from django.conf import settings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/14")
LIMIT = 200

settings.configure(
    INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "rest_framework"],
    CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}},
    REST_FRAMEWORK={"DEFAULT_THROTTLE_RATES": {"anon": f"{LIMIT}/day"}},
    REDIS_THROTTLE={"REDIS_URL": REDIS_URL},
    DATABASES={},
)
django.setup()

import redis  # noqa: E402
from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from rest_framework.request import Request  # noqa: E402
from rest_framework.throttling import AnonRateThrottle  # noqa: E402

from redis_throttle import ALGORITHMS, RateLimiter  # noqa: E402
from redis_throttle.drf import AnonRedisThrottle  # noqa: E402


def drf_request() -> Request:
    request = Request(RequestFactory().get("/", REMOTE_ADDR="198.51.100.1"))
    request._request.user = AnonymousUser()
    request._user = AnonymousUser()
    return request


def contend(throttle_cls, threads: int, hits: int) -> int:
    redis.Redis.from_url(REDIS_URL).flushdb()
    barrier = threading.Barrier(threads)
    totals: list[int] = []
    lock = threading.Lock()

    def worker():
        request = drf_request()
        barrier.wait()
        allowed = sum(throttle_cls().allow_request(request, None) for _ in range(hits))
        with lock:
            totals.append(allowed)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    return sum(totals)


def race(trials: int = 5) -> None:
    print(f"limit {LIMIT}/day, 800 attempts per trial, {trials} trials\n")
    print(f"{'throttle':<38}{'threads x hits':<16}allowed per trial")
    for threads, hits in [(1, 800), (4, 200), (8, 100), (32, 25)]:
        for name, cls in [
            ("rest_framework AnonRateThrottle", AnonRateThrottle),
            ("redis_throttle AnonRedisThrottle", AnonRedisThrottle),
        ]:
            allowed = [contend(cls, threads, hits) for _ in range(trials)]
            print(f"{name:<38}{f'{threads} x {hits}':<16}{allowed}")


def latency(samples: int = 5000) -> None:
    client = redis.Redis.from_url(REDIS_URL)
    client.flushdb()
    limiter = RateLimiter(client, prefix="bench")
    print(f"{samples} sequential checks per algorithm, one connection\n")
    print(f"{'algorithm':<18}{'p50 (ms)':>10}{'p99 (ms)':>10}{'checks/s':>12}")
    for algorithm in ALGORITHMS:
        timings = []
        started = time.perf_counter()
        for i in range(samples):
            t0 = time.perf_counter()
            limiter.hit(f"user:{i % 100}", "1000000/h", algorithm=algorithm)
            timings.append((time.perf_counter() - t0) * 1000)
        elapsed = time.perf_counter() - started
        q = statistics.quantiles(timings, n=100)
        print(f"{algorithm:<18}{q[49]:>10.3f}{q[98]:>10.3f}{samples / elapsed:>12,.0f}")


if __name__ == "__main__":
    {"race": race, "latency": latency}[sys.argv[1] if len(sys.argv) > 1 else "race"]()
