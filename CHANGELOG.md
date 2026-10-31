# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-31

### Added

- `RateLimiter` with atomic Lua scripts for fixed window, sliding window and token bucket (GCRA).
- Redis server clock for all checks; injectable clock for tests.
- `@ratelimit` decorator for function, class-based and async views, with stacking, dynamic rates, `block=False` and weighted `cost`.
- `RateLimitMiddleware` for settings-driven rules.
- DRF throttles: `AnonRedisThrottle`, `UserRedisThrottle`, `ScopedRedisThrottle`.
- `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` and `Retry-After` headers.
- Spoof-resistant client IP resolution and IPv6 /64 grouping.
- Fail-open or fail-closed behaviour with short default Redis timeouts.
- System checks for invalid settings.
- Benchmarks for contention and latency.
