# django-redis-throttle

[![CI](https://github.com/blitzlab/django-redis-throttle/actions/workflows/ci.yml/badge.svg)](https://github.com/blitzlab/django-redis-throttle/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Atomic, Redis-backed rate limiting for Django and Django REST Framework.

Every check runs as a single Lua script inside Redis, so concurrent requests across any number of workers can never both read the same counter and both get through. Three algorithms (fixed window, sliding window, token bucket), a view decorator, settings-driven middleware, and drop-in DRF throttle classes.

## Why

Django REST Framework's built-in throttles store request history in the cache with a separate read and write. Under concurrency, workers overwrite each other's updates and the limit stops holding. Measured with `benchmarks/run.py race` (limit 200, 800 attempts, 5 trials each):

| Throttle | 1 thread | 4 threads | 8 threads | 32 threads |
| --- | --- | --- | --- | --- |
| DRF `AnonRateThrottle` (Redis cache) | 200 | 522 to 583 | 800 | 800 |
| `redis_throttle.drf.AnonRedisThrottle` | 200 | 200 | 200 | 200 |

Each check is one Redis round trip: about 0.1 ms p50 and 0.2 ms p99 against a local Redis 7 (`benchmarks/run.py latency`, 2 vCPU container).

## Features

- **Atomic:** state is read and updated inside one Lua script per check.
- **One clock:** uses the Redis server's time, so app servers with skewed clocks still enforce one consistent limit.
- **Three algorithms:** `fixed_window`, `sliding_window` (default) and `token_bucket` (implemented as GCRA, no floating point drift).
- **Django and DRF:** `@ratelimit` for function, class-based and async views; `RateLimitMiddleware` for site-wide rules; DRF throttles that read your existing `DEFAULT_THROTTLE_RATES`.
- **Standard headers:** `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` and `Retry-After`.
- **Safe client IPs:** ignores spoofed `X-Forwarded-For` entries and groups IPv6 clients by /64.
- **Production defaults:** short Redis timeouts, fail open (configurable), keys that always expire, Redis Cluster hash tags, and system checks that catch bad config at startup.

## Install

```bash
pip install git+https://github.com/blitzlab/django-redis-throttle   # until the first PyPI release
```

```python
INSTALLED_APPS = [..., "redis_throttle"]  # optional, enables system checks

REDIS_THROTTLE = {"REDIS_URL": "redis://localhost:6379/0"}
```

Requires Python 3.10+, Django 5.2+, redis-py 5+, and Redis 6.2+.

## Usage

### Views

```python
from redis_throttle import ratelimit


@ratelimit("5/m", key="ip", methods=["POST"])
def login(request): ...


@ratelimit("1000/h", key="user_or_ip", algorithm="token_bucket")
@ratelimit("20/s", key="user_or_ip")  # stack limits; headers report the tightest
def search(request): ...
```

Rates look like `"100/m"`, `"5/10s"`, `"1000/hour"` or `"20/day"`. Keys can be `"ip"`, `"user"`, `"user_or_ip"`, `"header:X-Api-Key"`, a callable, or a dotted path to one. For class-based views use `method_decorator`.

Per-plan limits: pass a callable that returns a rate, or `None` for unlimited.

```python
def plan_rate(group, request):
    return None if request.user.plan == "enterprise" else "100/m"


@ratelimit(plan_rate, key="user")
def export(request): ...
```

With `block=False` the view always runs and can read `request.ratelimit` itself.

### Middleware

```python
MIDDLEWARE = [..., "redis_throttle.middleware.RateLimitMiddleware"]

REDIS_THROTTLE = {
    "RULES": [
        {"path": r"^/api/auth/", "methods": ["POST"], "rate": "10/m", "key": "ip"},
        {"path": r"^/api/", "rate": "5000/h", "key": "user_or_ip", "algorithm": "token_bucket"},
    ],
}
```

### Django REST Framework

```python
REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_CLASSES": [
        "redis_throttle.drf.AnonRedisThrottle",
        "redis_throttle.drf.UserRedisThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "100/hour", "user": "1000/hour", "uploads": "20/day"},
}
```

`ScopedRedisThrottle` works like DRF's `ScopedRateThrottle` and reads the view's `throttle_scope`. Add `RateLimitMiddleware` to get `RateLimit-*` headers on DRF responses.

### Anywhere else

The limiter is plain Python, so it also works in Celery tasks, management commands or around calls to third-party APIs:

```python
from redis_throttle import get_limiter

limiter = get_limiter()
decision = limiter.hit("stripe-api", "90/s", algorithm="token_bucket")
if not decision.allowed:
    raise self.retry(countdown=decision.retry_after)

limiter.hit(f"export:{user.pk}", "100/h", cost=25)  # weighted requests
limiter.peek(f"export:{user.pk}", "100/h")  # inspect without consuming
```

## Choosing an algorithm

| Algorithm | Memory per key | Bursts | Best for |
| --- | --- | --- | --- |
| `fixed_window` | 2 fields | Up to 2x the limit across a window boundary | Cheap quotas where boundary bursts don't matter |
| `sliding_window` | 3 fields | Smoothed; weights the previous window by overlap | General API limits (default) |
| `token_bucket` | 1 integer | Up to the limit at once, then a steady refill | Smoothing traffic to a downstream service |

See [docs/algorithms.md](docs/algorithms.md) for the maths and the trade-offs.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `REDIS_URL` | `redis://localhost:6379/0` | Connection URL |
| `CLIENT` | `None` | Dotted path to a callable returning a `redis.Redis`, to reuse a pool |
| `SOCKET_TIMEOUT` | `0.25` | Seconds before a slow Redis counts as down |
| `KEY_PREFIX` | `"rl"` | Prefix for all keys |
| `DEFAULT_ALGORITHM` | `"sliding_window"` | Used when a rule doesn't name one |
| `FAIL_OPEN` | `True` | Allow requests when Redis is unreachable; `False` rejects them |
| `TRUSTED_PROXY_DEPTH` | `0` | Reverse proxies in front of Django that append to `X-Forwarded-For` |
| `IPV6_PREFIX_LENGTH` | `64` | IPv6 clients are limited per prefix |
| `ENABLE_HEADERS` | `True` | Add `RateLimit-*` headers |
| `RATELIMITED_VIEW` | `None` | Dotted path to `view(request, decision)` for custom 429 responses |
| `RULES` | `[]` | Middleware rules |

## Development

```bash
docker run -d -p 6379:6379 redis:7
pip install -e . pytest pytest-django djangorestframework "ruff==0.17.3"
pytest
ruff check . && ruff format --check .
```

Tests run against a real Redis because the Lua scripts are the product. A fake clock steps through time, so the suite is fast and deterministic. CI covers Python 3.10 to 3.14, Django 5.2 to 6.1, and Redis 6.2 and 7.

## License

MIT
