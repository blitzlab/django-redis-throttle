from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps

from asgiref.sync import iscoroutinefunction, sync_to_async

from .core import RateSpec, apply_headers, check, ratelimited_response
from .keys import KeySpec

ALL_METHODS = None


def ratelimit(
    rate: RateSpec,
    *,
    key: KeySpec = "ip",
    group: str | None = None,
    algorithm: str | None = None,
    methods: Iterable[str] | None = ALL_METHODS,
    block: bool = True,
    cost: int = 1,
) -> Callable:
    """Rate limit a Django view (function, async, or class-based via method_decorator).

        @ratelimit("10/m", key="user_or_ip", methods=["POST"])
        def create_comment(request): ...

    `rate` can be a callable `(group, request) -> rate | None` for per-plan
    limits. With `block=False` the view always runs and can inspect
    `request.ratelimit` itself. Decorators stack: headers report the most
    restrictive limit.
    """
    allowed_methods = {m.upper() for m in methods} if methods is not None else None

    def decorator(view: Callable) -> Callable:
        view_group = group or f"{view.__module__}.{view.__qualname__}"

        def applies(request) -> bool:
            return allowed_methods is None or request.method in allowed_methods

        def run_check(request):
            return check(request, group=view_group, rate=rate, key=key, algorithm=algorithm, cost=cost)

        if iscoroutinefunction(view):

            @wraps(view)
            async def async_wrapper(request, *args, **kwargs):
                decision = await sync_to_async(run_check, thread_sensitive=False)(request) if applies(request) else None
                if decision is not None and not decision.allowed and block:
                    return ratelimited_response(request, decision)
                response = await view(request, *args, **kwargs)
                return _finish(request, response, decision)

            return async_wrapper

        @wraps(view)
        def wrapper(request, *args, **kwargs):
            decision = run_check(request) if applies(request) else None
            if decision is not None and not decision.allowed and block:
                return ratelimited_response(request, decision)
            response = view(request, *args, **kwargs)
            return _finish(request, response, decision)

        return wrapper

    return decorator


def _finish(request, response, decision):
    if decision is not None:
        # Stacked decorators each finish in turn; always report the tightest one.
        apply_headers(response, request.ratelimit)
    return response
