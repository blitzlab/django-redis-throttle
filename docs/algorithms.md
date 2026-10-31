# Algorithms

All three algorithms share the same contract. Each one is a Lua script that reads and updates a single Redis key and returns `{allowed, remaining, reset_after_ms, retry_after_ms}`. Redis runs a script to completion before it serves any other command, so a check is atomic no matter how many workers call it at once.

## Why atomicity matters

A limiter that does `GET count`, compares, then `SET count + 1` has a window between the read and the write. Two workers can read the same value, both decide they are under the limit, and both write. DRF's cache-based throttles keep a list of timestamps and have the same problem with a bigger window: concurrent workers overwrite each other's lists, so the stored history grows far slower than real traffic. `benchmarks/run.py race` measures this; with 8 concurrent workers the built-in throttle stops limiting at all.

## Where time comes from

Scripts call Redis `TIME`, so every check across the fleet uses one clock. If each app server used its own clock, a server running a few seconds fast would see windows roll over early and admit extra requests. Tests pass an explicit `now_ms` instead, which lets the suite step through time deterministically.

## Fixed window

State: `{w: window index, c: count}`.

`window = floor(now / period)`. If the stored window differs, the count restarts at zero. A request is allowed when `count + cost <= limit`.

Simple and cheap, but a client can send `limit` requests at the end of one window and `limit` more at the start of the next, so up to 2x the limit can land within a few milliseconds. `tests/test_algorithms.py::TestFixedWindow::test_allows_a_double_burst_across_the_boundary` keeps that behaviour documented.

## Sliding window (default)

State: `{w: window index, c: current count, p: previous count}`.

The sliding window over the last `period` overlaps the current fixed window and part of the previous one. The estimate weights the previous window by how much of it is still inside the sliding window:

```
elapsed   = now - window_start
estimated = previous * (period - elapsed) / period + current
allowed   = estimated + cost <= limit
```

This assumes requests in the previous window were evenly spread, which is accurate enough for API limits and costs two integers per key instead of one entry per request (a sorted-set log would be exact but grows with the limit).

`retry_after` is solved exactly rather than guessed:

- If the current window alone still has room (`current + cost <= limit`), wait until enough of the previous window has slid out: `t = (period - elapsed) - (limit - current - cost) * period / previous`.
- Otherwise the current window must first become the previous window and then decay: `t = (period - elapsed) + max(0, period - (limit - cost) * period / current)`.

Both are rounded up to the next millisecond. The tests check that a request just before `retry_after` is denied and one at `retry_after` is allowed.

## Token bucket (GCRA)

State: one integer, the theoretical arrival time (TAT).

A token bucket holds `limit` tokens and refills at `limit / period`. Tracking fractional tokens in Redis loses precision (Lua numbers are written back as strings with 14 significant digits), which can make a client wait slightly longer than its `retry_after` said. The generic cell rate algorithm describes the same bucket with one timestamp:

```
interval = period / limit              # ms to earn back one token
tat      = max(stored_tat, now)
new_tat  = ceil(tat + cost * interval)
allow_at = new_tat - period            # the bucket can absorb `period` worth of debt
allowed  = now >= allow_at
```

When allowed, `new_tat` is stored with a TTL of `new_tat - now`, which is exactly when the bucket is full again. A missing key means a full bucket, so idle clients cost no memory. Rounding the stored TAT up to whole milliseconds keeps every comparison exact and never admits a request early.

## Keys

`{prefix}:{algorithm}:{limit}/{period}:{identity}`. The identity is wrapped in a `{...}` hash tag so each limiter lives in one Redis Cluster slot. Identities longer than 200 characters are replaced by their SHA-256. Every write sets a TTL, so no key outlives the window it describes.

## Failure handling

The default client uses 250 ms socket timeouts. If Redis is unreachable the check returns a `Decision` with `degraded=True`, and `FAIL_OPEN` decides whether that request is allowed. Degraded decisions add no `RateLimit-*` headers, because there is no real state to report.
