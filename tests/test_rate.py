import pytest

from redis_throttle import Rate, parse_rate


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("100/m", Rate(100, 60)),
        ("100/min", Rate(100, 60)),
        ("100/minute", Rate(100, 60)),
        ("100/minutes", Rate(100, 60)),
        ("10/s", Rate(10, 1)),
        ("5/10s", Rate(5, 10)),
        ("30/15m", Rate(30, 900)),
        ("1000/hour", Rate(1000, 3600)),
        ("20/day", Rate(20, 86400)),
        (" 7 / 2 h ", Rate(7, 7200)),
        ("3/M", Rate(3, 60)),
    ],
)
def test_parses_supported_formats(value, expected):
    assert parse_rate(value) == expected


@pytest.mark.parametrize("value", ["", "100", "/m", "0/m", "10/0s", "10/fortnight", "ten/m", "-1/m", "10/m/s"])
def test_rejects_invalid_rates(value):
    with pytest.raises(ValueError, match="Invalid rate"):
        parse_rate(value)


def test_rejects_non_strings():
    with pytest.raises(TypeError):
        parse_rate(100)


def test_rate_objects_pass_through():
    rate = Rate(5, 60)
    assert parse_rate(rate) is rate
    assert rate.period_ms == 60_000
    assert str(rate) == "5/60s"
