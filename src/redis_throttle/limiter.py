"""Core rate limiter. Framework-agnostic: pass any redis.Redis client."""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

import redis
from django.core.signals import setting_changed

from .rate import Rate, parse_rate
from .scripts import SCRIPTS

logger = logging.getLogger("redis_throttle")

ALGORITHMS = tuple(SCRIPTS)
_MAX_KEY_LENGTH = 200


@dataclass(frozen=True)
class Decision:
    """The outcome of one rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_after: float
    retry_after: float
    rate: Rate = field(compare=False)
    algorithm: str = field(compare=False)
    key: str = field(compare=False)
    degraded: bool = False  # True when Redis was unreachable and FAIL_OPEN applied

    def headers(self) -> dict[str, str]:
        headers = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(max(0, self.remaining)),
            "RateLimit-Reset": str(math.ceil(self.reset_after)),
        }
        if not self.allowed:
            headers["Retry-After"] = str(max(1, math.ceil(self.retry_after)))
        return headers


class RateLimiter:
    """Atomic rate limiting backed by Redis Lua scripts.

    >>> limiter = RateLimiter(redis.Redis())
    >>> limiter.hit("user:42", "100/m").allowed
    True
    """

    def __init__(
        self,
        client: redis.Redis,
        *,
        prefix: str = "rl",
        fail_open: bool = True,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.client = client
        self.prefix = prefix
        self.fail_open = fail_open
        # Milliseconds since the epoch. None (the default) uses the Redis server
        # clock; tests inject a fake clock to step through time deterministically.
        self.clock = clock
        self._scripts = {name: client.register_script(source) for name, source in SCRIPTS.items()}

    def make_key(self, key: str, rate: Rate, algorithm: str) -> str:
        if len(key) > _MAX_KEY_LENGTH:
            key = "sha256:" + hashlib.sha256(key.encode()).hexdigest()
        # The {...} hash tag pins all of a limiter's state to one cluster slot.
        return f"{self.prefix}:{algorithm}:{rate.limit}/{rate.period}:{{{key}}}"

    def hit(self, key: str, rate: str | Rate, *, algorithm: str = "sliding_window", cost: int = 1) -> Decision:
        """Consume `cost` units for `key` and report whether the request is allowed."""
        rate = parse_rate(rate)
        if algorithm not in SCRIPTS:
            raise ValueError(f"Unknown algorithm {algorithm!r}. Choose one of: {', '.join(ALGORITHMS)}.")
        if not 0 <= cost <= rate.limit:
            raise ValueError(f"cost must be between 0 and the limit ({rate.limit}), got {cost}.")

        redis_key = self.make_key(key, rate, algorithm)
        now = "" if self.clock is None else str(int(self.clock()))
        try:
            allowed, remaining, reset_ms, retry_ms = self._scripts[algorithm](
                keys=[redis_key], args=[rate.limit, rate.period_ms, cost, now]
            )
        except redis.RedisError:
            logger.warning(
                "rate limit backend unavailable for %s; fail_open=%s", redis_key, self.fail_open, exc_info=True
            )
            return Decision(
                allowed=self.fail_open,
                limit=rate.limit,
                remaining=rate.limit if self.fail_open else 0,
                reset_after=0.0,
                retry_after=0.0 if self.fail_open else 1.0,
                rate=rate,
                algorithm=algorithm,
                key=redis_key,
                degraded=True,
            )

        return Decision(
            allowed=bool(allowed),
            limit=rate.limit,
            remaining=int(remaining),
            reset_after=reset_ms / 1000,
            retry_after=retry_ms / 1000,
            rate=rate,
            algorithm=algorithm,
            key=redis_key,
        )

    def peek(self, key: str, rate: str | Rate, *, algorithm: str = "sliding_window") -> Decision:
        """Report the current state without consuming anything."""
        return self.hit(key, rate, algorithm=algorithm, cost=0)

    def reset(self, key: str, rate: str | Rate, *, algorithm: str = "sliding_window") -> None:
        self.client.delete(self.make_key(key, parse_rate(rate), algorithm))


@lru_cache(maxsize=1)
def get_limiter() -> RateLimiter:
    """The process-wide limiter configured from Django settings."""
    from django.utils.module_loading import import_string

    from .conf import get_setting

    factory = get_setting("CLIENT")
    if factory:
        client = import_string(factory)()
    else:
        # Short timeouts so an unhealthy Redis degrades to FAIL_OPEN quickly
        # instead of stalling every request behind it.
        timeout = get_setting("SOCKET_TIMEOUT")
        client = redis.Redis.from_url(get_setting("REDIS_URL"), socket_timeout=timeout, socket_connect_timeout=timeout)
    return RateLimiter(client, prefix=get_setting("KEY_PREFIX"), fail_open=get_setting("FAIL_OPEN"))


def _reset_limiter(*, setting: str, **kwargs) -> None:
    if setting == "REDIS_THROTTLE":
        get_limiter.cache_clear()


setting_changed.connect(_reset_limiter)
