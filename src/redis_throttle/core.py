"""Shared plumbing for the decorator, middleware and DRF integration."""

from __future__ import annotations

import math
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.module_loading import import_string

from .conf import get_setting
from .keys import KeySpec, resolve_key
from .limiter import Decision, get_limiter
from .rate import Rate

RateSpec = str | Rate | Callable[[str, HttpRequest], "str | Rate | None"]


def check(
    request: HttpRequest,
    *,
    group: str,
    rate: RateSpec,
    key: KeySpec = "ip",
    algorithm: str | None = None,
    cost: int = 1,
) -> Decision | None:
    """Run one rate limit check and record it on the request.

    Returns None when the rule does not apply (no identity, or a dynamic rate
    that returned None for this request).
    """
    resolved_rate = rate(group, request) if callable(rate) else rate
    if resolved_rate is None:
        return None
    identity = resolve_key(key, request)
    if identity is None:
        return None

    decision = get_limiter().hit(
        f"{group}:{identity}",
        resolved_rate,
        algorithm=algorithm or get_setting("DEFAULT_ALGORITHM"),
        cost=cost,
    )
    record(request, decision)
    return decision


def record(request: HttpRequest, decision: Decision) -> None:
    decisions = getattr(request, "ratelimits", None)
    if decisions is None:
        decisions = []
        request.ratelimits = decisions
    decisions.append(decision)
    request.ratelimit = most_restrictive(decisions)


def most_restrictive(decisions: list[Decision]) -> Decision:
    """A denial beats an allowance; otherwise the fewest requests remaining wins."""
    return min(decisions, key=lambda d: (d.allowed, d.remaining / d.limit if d.limit else 0))


def apply_headers(response: HttpResponse, decision: Decision) -> HttpResponse:
    if get_setting("ENABLE_HEADERS") and not decision.degraded:
        for name, value in decision.headers().items():
            response[name] = value
    return response


def ratelimited_response(request: HttpRequest, decision: Decision) -> HttpResponse:
    custom = get_setting("RATELIMITED_VIEW")
    if custom:
        response = import_string(custom)(request, decision)
    else:
        response = JsonResponse(
            {"detail": "Too many requests.", "retry_after": max(1, math.ceil(decision.retry_after))},
            status=429,
        )
    return apply_headers(response, decision)
