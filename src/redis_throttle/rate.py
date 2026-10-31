"""Rate strings: "100/m", "5/10s", "1000/hour", "20/day"."""

from __future__ import annotations

import re
from dataclasses import dataclass

_UNITS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "h": 3600,
    "hour": 3600,
    "d": 86400,
    "day": 86400,
}

_RATE_RE = re.compile(r"^\s*(?P<limit>\d+)\s*/\s*(?P<multiplier>\d*)\s*(?P<unit>[a-zA-Z]+)\s*$")


@dataclass(frozen=True)
class Rate:
    """`limit` requests per `period` seconds."""

    limit: int
    period: int

    @property
    def period_ms(self) -> int:
        return self.period * 1000

    def __str__(self) -> str:
        return f"{self.limit}/{self.period}s"


def _normalise_unit(unit: str) -> str:
    unit = unit.lower()
    if unit not in _UNITS and unit.endswith("s") and unit[:-1] in _UNITS:
        unit = unit[:-1]  # "minutes" -> "minute", "secs" -> "sec"
    return unit


def parse_rate(value: str | Rate) -> Rate:
    """Parse a rate string into a :class:`Rate`.

    Accepts DRF-style strings ("100/minute", "1000/day") and an optional
    multiplier for custom windows ("5/10s", "30/15m").
    """
    if isinstance(value, Rate):
        return value
    if not isinstance(value, str):
        raise TypeError(f"rate must be a string or Rate, got {type(value).__name__}")

    match = _RATE_RE.match(value)
    unit = _normalise_unit(match.group("unit")) if match else ""
    if not match or unit not in _UNITS:
        raise ValueError(f"Invalid rate {value!r}. Use forms like '100/m', '5/10s', '1000/hour'.")

    limit = int(match.group("limit"))
    multiplier = int(match.group("multiplier") or 1)
    if limit < 1 or multiplier < 1:
        raise ValueError(f"Invalid rate {value!r}: limit and window must be positive.")
    return Rate(limit=limit, period=multiplier * _UNITS[unit])
