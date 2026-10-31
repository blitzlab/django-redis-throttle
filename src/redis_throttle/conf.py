"""Settings, read from ``settings.REDIS_THROTTLE`` with these defaults."""

from __future__ import annotations

from typing import Any

from django.conf import settings

DEFAULTS: dict[str, Any] = {
    # Connection: either a URL, or a dotted path to a zero-argument callable
    # that returns a redis.Redis client (to reuse an existing pool).
    "REDIS_URL": "redis://localhost:6379/0",
    "CLIENT": None,
    "SOCKET_TIMEOUT": 0.25,
    "KEY_PREFIX": "rl",
    "DEFAULT_ALGORITHM": "sliding_window",
    # When Redis is unreachable: True lets traffic through, False rejects it.
    "FAIL_OPEN": True,
    # Number of reverse proxies in front of Django that append to X-Forwarded-For.
    # 0 trusts only REMOTE_ADDR, which is the only safe default.
    "TRUSTED_PROXY_DEPTH": 0,
    # IPv6 clients usually control a whole /64, so limit them per prefix.
    "IPV6_PREFIX_LENGTH": 64,
    "ENABLE_HEADERS": True,
    # Dotted path to a view(request, decision) that renders the 429 response.
    "RATELIMITED_VIEW": None,
    # Global rules applied by RateLimitMiddleware.
    "RULES": [],
}


def get_setting(name: str) -> Any:
    user_settings = getattr(settings, "REDIS_THROTTLE", {}) or {}
    return user_settings.get(name, DEFAULTS[name])
