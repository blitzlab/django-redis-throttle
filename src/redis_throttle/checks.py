"""System checks: catch a bad rate or algorithm at startup, not on first request."""

from __future__ import annotations

from django.core.checks import Error, Tags, register

from .conf import get_setting
from .limiter import ALGORITHMS
from .rate import parse_rate


@register(Tags.security)
def check_settings(app_configs=None, **kwargs) -> list[Error]:
    errors: list[Error] = []

    algorithm = get_setting("DEFAULT_ALGORITHM")
    if algorithm not in ALGORITHMS:
        errors.append(
            Error(
                f"REDIS_THROTTLE['DEFAULT_ALGORITHM'] is {algorithm!r}.",
                hint=f"Use one of: {', '.join(ALGORITHMS)}.",
                id="redis_throttle.E001",
            )
        )

    depth = get_setting("TRUSTED_PROXY_DEPTH")
    if not isinstance(depth, int) or depth < 0:
        errors.append(
            Error(
                "REDIS_THROTTLE['TRUSTED_PROXY_DEPTH'] must be a non-negative integer.",
                id="redis_throttle.E002",
            )
        )

    for index, rule in enumerate(get_setting("RULES")):
        label = f"REDIS_THROTTLE['RULES'][{index}]"
        missing = {"path", "rate"} - set(rule)
        if missing:
            errors.append(Error(f"{label} is missing {sorted(missing)}.", id="redis_throttle.E003"))
            continue
        try:
            parse_rate(rule["rate"])
        except (TypeError, ValueError) as exc:
            errors.append(Error(f"{label}: {exc}", id="redis_throttle.E004"))
        if rule.get("algorithm") not in (None, *ALGORITHMS):
            errors.append(
                Error(
                    f"{label} uses unknown algorithm {rule['algorithm']!r}.",
                    hint=f"Use one of: {', '.join(ALGORITHMS)}.",
                    id="redis_throttle.E005",
                )
            )
    return errors
