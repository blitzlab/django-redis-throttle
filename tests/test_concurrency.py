"""Atomicity under contention: the guarantee a read-then-write limiter can't make."""

import threading

import pytest
import redis
from django.conf import settings

from redis_throttle import ALGORITHMS, RateLimiter

THREADS = 32
HITS_PER_THREAD = 25
LIMIT = 200


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_concurrent_hits_never_exceed_the_limit(redis_client, algorithm):
    # Each thread gets its own connection, like separate web workers would.
    barrier = threading.Barrier(THREADS)
    allowed = []
    lock = threading.Lock()

    def worker():
        limiter = RateLimiter(redis.Redis.from_url(settings.REDIS_URL), prefix="test")
        barrier.wait()
        local = sum(limiter.hit("shared", f"{LIMIT}/day", algorithm=algorithm).allowed for _ in range(HITS_PER_THREAD))
        with lock:
            allowed.append(local)

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 800 attempts against a limit of 200: exactly 200 may pass, never more.
    assert sum(allowed) == LIMIT
