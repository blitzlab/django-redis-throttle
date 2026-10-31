"""Site-wide rules, configured in settings instead of on each view.

    REDIS_THROTTLE = {
        "RULES": [
            {"path": r"^/api/auth/login/$", "methods": ["POST"], "rate": "5/m", "key": "ip"},
            {"path": r"^/api/", "rate": "1000/h", "key": "user_or_ip", "algorithm": "token_bucket"},
        ],
    }

Every matching rule is checked; the first denial short-circuits with a 429.
The middleware also adds RateLimit headers for limits recorded by the
decorator or the DRF throttles further down the stack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async

from .conf import get_setting
from .core import apply_headers, check, ratelimited_response


@dataclass(frozen=True)
class Rule:
    group: str
    pattern: re.Pattern
    methods: frozenset[str] | None
    rate: str
    key: str
    algorithm: str | None

    @classmethod
    def from_setting(cls, index: int, raw: dict) -> Rule:
        methods = raw.get("methods")
        return cls(
            group=raw.get("group") or f"rule{index}:{raw['path']}",
            pattern=re.compile(raw["path"]),
            methods=frozenset(m.upper() for m in methods) if methods else None,
            rate=raw["rate"],
            key=raw.get("key", "ip"),
            algorithm=raw.get("algorithm"),
        )

    def matches(self, request) -> bool:
        return (self.methods is None or request.method in self.methods) and bool(self.pattern.search(request.path))


class RateLimitMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.rules = [Rule.from_setting(i, raw) for i, raw in enumerate(get_setting("RULES"))]
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        if iscoroutinefunction(self):
            return self.__acall__(request)
        denied = self._check_rules(request)
        if denied is not None:
            return denied
        return self._finish(request, self.get_response(request))

    async def __acall__(self, request):
        denied = await sync_to_async(self._check_rules, thread_sensitive=False)(request)
        if denied is not None:
            return denied
        return self._finish(request, await self.get_response(request))

    def _check_rules(self, request):
        for rule in self.rules:
            if not rule.matches(request):
                continue
            decision = check(request, group=rule.group, rate=rule.rate, key=rule.key, algorithm=rule.algorithm)
            if decision is not None and not decision.allowed:
                return ratelimited_response(request, decision)
        return None

    @staticmethod
    def _finish(request, response):
        # DRF wraps the request; its throttles record decisions on the inner one.
        decision = getattr(request, "ratelimit", None)
        if decision is not None and "RateLimit-Limit" not in response:
            apply_headers(response, decision)
        return response
