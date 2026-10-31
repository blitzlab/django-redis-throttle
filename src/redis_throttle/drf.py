"""Django REST Framework throttles backed by the same atomic Redis limiter.

Drop-in replacements for DRF's cache-based throttles, which read and write the
counter in two separate cache calls and can therefore over-admit under
concurrency. Rates come from DEFAULT_THROTTLE_RATES as usual and accept the
extended syntax too ("5/10s").

    REST_FRAMEWORK = {
        "DEFAULT_THROTTLE_CLASSES": [
            "redis_throttle.drf.AnonRedisThrottle",
            "redis_throttle.drf.UserRedisThrottle",
        ],
        "DEFAULT_THROTTLE_RATES": {"anon": "100/hour", "user": "1000/hour"},
    }
"""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle

from .conf import get_setting
from .core import record
from .keys import client_ip
from .limiter import Decision, get_limiter
from .rate import parse_rate


class RedisRateThrottle(SimpleRateThrottle):
    """Base class. Subclasses set `scope` and implement `get_cache_key`."""

    algorithm: str | None = None
    decision: Decision | None = None

    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        parsed = parse_rate(rate)
        return (parsed.limit, parsed.period)

    def allow_request(self, request, view) -> bool:
        if self.rate is None:
            return True
        key = self.get_cache_key(request, view)
        if key is None:
            return True
        self.decision = get_limiter().hit(key, self.rate, algorithm=self.algorithm or get_setting("DEFAULT_ALGORITHM"))
        record(getattr(request, "_request", request), self.decision)
        return self.decision.allowed

    def wait(self) -> float | None:
        if self.decision is None or self.decision.allowed:
            return None
        return self.decision.retry_after

    def ident(self, request) -> str:
        return f"ip:{client_ip(getattr(request, '_request', request))}"


class AnonRedisThrottle(RedisRateThrottle):
    """Limits unauthenticated requests by client IP."""

    scope = "anon"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return None
        return f"drf:{self.scope}:{self.ident(request)}"


class UserRedisThrottle(RedisRateThrottle):
    """Limits authenticated users by id, and anonymous users by IP."""

    scope = "user"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"drf:{self.scope}:user:{request.user.pk}"
        return f"drf:{self.scope}:{self.ident(request)}"


class ScopedRedisThrottle(RedisRateThrottle):
    """Uses the view's `throttle_scope`, like DRF's ScopedRateThrottle."""

    scope_attr = "throttle_scope"

    def __init__(self):
        # The scope is only known once the view is, so defer rate lookup.
        pass

    def allow_request(self, request, view) -> bool:
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        authenticated = request.user and request.user.is_authenticated
        identity = f"user:{request.user.pk}" if authenticated else self.ident(request)
        return f"drf:{self.scope}:{identity}"
