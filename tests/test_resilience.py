"""Behaviour when Redis is unreachable."""

import time

import redis

from redis_throttle import RateLimiter, get_limiter

DEAD_REDIS = "redis://127.0.0.1:1/0"


def unreachable_client():
    return redis.Redis.from_url(DEAD_REDIS, socket_connect_timeout=0.1, socket_timeout=0.1)


def test_fail_open_lets_traffic_through_and_flags_the_decision():
    decision = RateLimiter(unreachable_client(), fail_open=True).hit("k", "1/m")
    assert decision.allowed
    assert decision.degraded


def test_fail_closed_rejects_traffic():
    decision = RateLimiter(unreachable_client(), fail_open=False).hit("k", "1/m")
    assert not decision.allowed
    assert decision.degraded
    assert decision.headers()["Retry-After"] == "1"


def test_default_client_uses_short_timeouts(settings):
    settings.REDIS_THROTTLE = {**settings.REDIS_THROTTLE, "REDIS_URL": DEAD_REDIS, "SOCKET_TIMEOUT": 0.2}
    started = time.monotonic()
    decision = get_limiter().hit("k", "1/m")
    assert decision.degraded
    assert time.monotonic() - started < 2


def test_degraded_decisions_add_no_headers(client, settings):
    settings.REDIS_THROTTLE = {**settings.REDIS_THROTTLE, "REDIS_URL": DEAD_REDIS, "SOCKET_TIMEOUT": 0.1}
    response = client.get("/limited/")
    assert response.status_code == 200
    assert "RateLimit-Limit" not in response
