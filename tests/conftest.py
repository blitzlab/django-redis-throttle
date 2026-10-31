import pytest
import redis
from django.conf import settings

from redis_throttle import RateLimiter, get_limiter


class FakeClock:
    """Milliseconds since the epoch, advanced by hand."""

    def __init__(self, start_ms: int = 1_700_000_000_000):
        # Start exactly on a minute boundary so window maths is easy to read.
        self.now = start_ms - start_ms % 60_000

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * 1000)


@pytest.fixture
def redis_client():
    client = redis.Redis.from_url(settings.REDIS_URL)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Redis is not available at REDIS_URL")
    client.flushdb()
    yield client
    client.flushdb()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(redis_client, clock):
    return RateLimiter(redis_client, prefix="test", clock=clock)


@pytest.fixture(autouse=True)
def fresh_limiter():
    get_limiter.cache_clear()
    yield
    get_limiter.cache_clear()
