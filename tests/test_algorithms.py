"""Behaviour of each algorithm, stepped through time with a fake clock."""

import pytest

from redis_throttle import ALGORITHMS

pytestmark = pytest.mark.usefixtures("redis_client")


def drain(limiter, key, rate, algorithm, n):
    return [limiter.hit(key, rate, algorithm=algorithm).allowed for _ in range(n)]


class TestCommonContract:
    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_allows_up_to_the_limit_then_denies(self, limiter, algorithm):
        assert drain(limiter, "k", "5/m", algorithm, 7) == [True] * 5 + [False] * 2

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_remaining_counts_down(self, limiter, algorithm):
        remaining = [limiter.hit("k", "3/m", algorithm=algorithm).remaining for _ in range(3)]
        assert remaining == [2, 1, 0]

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_waiting_retry_after_lets_the_next_request_through(self, limiter, clock, algorithm):
        drain(limiter, "k", "4/m", algorithm, 4)
        denied = limiter.hit("k", "4/m", algorithm=algorithm)
        assert not denied.allowed
        assert denied.retry_after > 0

        clock.advance(denied.retry_after - 0.01)
        assert not limiter.hit("k", "4/m", algorithm=algorithm).allowed, "allowed before retry_after elapsed"

        clock.advance(0.01)
        assert limiter.hit("k", "4/m", algorithm=algorithm).allowed, "still denied after retry_after"

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_keys_are_independent(self, limiter, algorithm):
        drain(limiter, "alice", "2/m", algorithm, 2)
        assert not limiter.hit("alice", "2/m", algorithm=algorithm).allowed
        assert limiter.hit("bob", "2/m", algorithm=algorithm).allowed

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_cost_consumes_multiple_units(self, limiter, algorithm):
        assert limiter.hit("k", "10/m", algorithm=algorithm, cost=7).allowed
        assert not limiter.hit("k", "10/m", algorithm=algorithm, cost=4).allowed
        assert limiter.hit("k", "10/m", algorithm=algorithm, cost=3).allowed

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_cost_above_limit_is_rejected(self, limiter, algorithm):
        with pytest.raises(ValueError, match="cost"):
            limiter.hit("k", "3/m", algorithm=algorithm, cost=4)

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_peek_does_not_consume(self, limiter, algorithm):
        limiter.hit("k", "3/m", algorithm=algorithm)
        for _ in range(5):
            assert limiter.peek("k", "3/m", algorithm=algorithm).remaining == 2
        assert drain(limiter, "k", "3/m", algorithm, 3) == [True, True, False]

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_reset_clears_state(self, limiter, algorithm):
        drain(limiter, "k", "2/m", algorithm, 2)
        limiter.reset("k", "2/m", algorithm=algorithm)
        assert limiter.hit("k", "2/m", algorithm=algorithm).allowed

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_state_keys_always_expire(self, limiter, redis_client, algorithm):
        decision = limiter.hit("k", "5/m", algorithm=algorithm)
        ttl_ms = redis_client.pttl(decision.key)
        assert 0 < ttl_ms <= 120_000

    def test_unknown_algorithm_is_rejected(self, limiter):
        with pytest.raises(ValueError, match="Unknown algorithm"):
            limiter.hit("k", "5/m", algorithm="leaky")

    def test_long_keys_are_hashed_and_cluster_tagged(self, limiter):
        decision = limiter.hit("x" * 500, "5/m", algorithm="fixed_window")
        assert len(decision.key) < 120
        assert "{sha256:" in decision.key


class TestFixedWindow:
    def test_counter_resets_on_the_window_boundary(self, limiter, clock):
        drain(limiter, "k", "3/m", "fixed_window", 3)
        clock.advance(59.999)
        assert not limiter.hit("k", "3/m", algorithm="fixed_window").allowed
        clock.advance(0.001)
        assert drain(limiter, "k", "3/m", "fixed_window", 4) == [True, True, True, False]

    def test_allows_a_double_burst_across_the_boundary(self, limiter, clock):
        # The known weakness of fixed windows, kept as documentation.
        clock.advance(59)
        first = drain(limiter, "k", "10/m", "fixed_window", 10)
        clock.advance(1)
        second = drain(limiter, "k", "10/m", "fixed_window", 10)
        assert all(first + second)  # 20 requests in ~1 second


class TestSlidingWindow:
    def test_previous_window_is_weighted_by_overlap(self, limiter, clock):
        drain(limiter, "k", "10/m", "sliding_window", 10)
        clock.advance(90)  # halfway through the next window: estimate = 10 * 0.5 = 5
        assert drain(limiter, "k", "10/m", "sliding_window", 6) == [True] * 5 + [False]

    def test_blocks_the_boundary_burst_fixed_window_allows(self, limiter, clock):
        clock.advance(59)
        assert all(drain(limiter, "k", "10/m", "sliding_window", 10))
        clock.advance(1)  # previous window still weighs ~98%
        assert not any(drain(limiter, "k", "10/m", "sliding_window", 10))

    def test_state_older_than_one_window_is_forgotten(self, limiter, clock):
        drain(limiter, "k", "3/m", "sliding_window", 3)
        clock.advance(120)
        assert drain(limiter, "k", "3/m", "sliding_window", 3) == [True] * 3


class TestTokenBucket:
    def test_refills_continuously(self, limiter, clock):
        drain(limiter, "k", "10/10s", "token_bucket", 10)  # 1 token per second
        assert not limiter.hit("k", "10/10s", algorithm="token_bucket").allowed
        clock.advance(1)
        assert drain(limiter, "k", "10/10s", "token_bucket", 2) == [True, False]
        clock.advance(3.5)
        assert drain(limiter, "k", "10/10s", "token_bucket", 4) == [True, True, True, False]

    def test_never_exceeds_capacity(self, limiter, clock):
        clock.advance(3600)
        assert drain(limiter, "k", "5/m", "token_bucket", 6) == [True] * 5 + [False]

    def test_reset_after_reports_time_until_full(self, limiter):
        drain(limiter, "k", "10/10s", "token_bucket", 4)
        decision = limiter.peek("k", "10/10s", algorithm="token_bucket")
        assert decision.reset_after == pytest.approx(4.0)

    def test_key_expires_exactly_when_the_bucket_is_full_again(self, limiter, redis_client):
        drain(limiter, "k", "10/10s", "token_bucket", 4)
        decision = limiter.peek("k", "10/10s", algorithm="token_bucket")
        # A missing key means a full bucket, so state lives only as long as it matters.
        assert redis_client.pttl(decision.key) == pytest.approx(4000, abs=50)

    def test_uneven_intervals_never_admit_early(self, limiter, clock):
        # 7/m gives a fractional interval (8571.4 ms per token).
        drain(limiter, "k", "7/m", "token_bucket", 7)
        for _ in range(20):
            denied = limiter.hit("k", "7/m", algorithm="token_bucket")
            assert not denied.allowed
            clock.advance(denied.retry_after - 0.001)
            assert not limiter.hit("k", "7/m", algorithm="token_bucket").allowed
            clock.advance(0.001)
            assert limiter.hit("k", "7/m", algorithm="token_bucket").allowed


def test_server_clock_is_used_when_no_clock_is_injected(redis_client):
    from redis_throttle import RateLimiter

    limiter = RateLimiter(redis_client, prefix="test")
    decision = limiter.hit("k", "2/m", algorithm="sliding_window")
    assert decision.allowed
    assert 0 < decision.reset_after <= 60
