"""Tests for the token bucket limiter: semantics, edge cases, eviction, threads."""
from __future__ import annotations

import math
import unittest
from concurrent.futures import ThreadPoolExecutor

from solution.main import RateLimiter, TokenBucket


class FakeClock:
    """A clock you control — this is what keeps the suite fast and flake-free."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTokenBucket(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.bucket = TokenBucket(capacity=3, refill_rate=1, clock=self.clock)

    def test_burst_then_throttle(self) -> None:
        self.assertTrue(all(self.bucket.take().allowed for _ in range(3)))
        denied = self.bucket.take()
        self.assertFalse(denied.allowed)
        self.assertAlmostEqual(denied.retry_after, 1.0)
        self.assertAlmostEqual(denied.remaining, 0.0)

    def test_refills_over_time(self) -> None:
        for _ in range(3):
            self.bucket.take()
        self.clock.advance(0.5)
        self.assertFalse(self.bucket.take().allowed)  # only half a token back
        self.clock.advance(0.5)
        decision = self.bucket.take()
        self.assertTrue(decision.allowed)
        self.assertAlmostEqual(decision.remaining, 0.0)

    def test_refill_is_capped_at_capacity(self) -> None:
        for _ in range(3):
            self.bucket.take()
        self.clock.advance(10_000)
        self.assertAlmostEqual(self.bucket.tokens, 3.0)
        self.assertEqual([self.bucket.take().allowed for _ in range(4)], [True, True, True, False])

    def test_retry_after_math_with_weighted_cost(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(capacity=2, refill_rate=0.5, clock=clock)
        bucket.take(2)
        decision = bucket.take(1)
        self.assertFalse(decision.allowed)
        self.assertAlmostEqual(decision.retry_after, 2.0)  # 1 missing token / 0.5 per second

    def test_cost_larger_than_capacity_never_succeeds(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(capacity=2, refill_rate=1, clock=clock)
        denied = bucket.take(5)
        self.assertFalse(denied.allowed)
        self.assertAlmostEqual(denied.retry_after, 3.0)  # (5 - 2) / 1
        clock.advance(1_000)
        self.assertFalse(bucket.take(5).allowed)

    def test_zero_refill_rate_means_infinite_wait(self) -> None:
        bucket = TokenBucket(capacity=1, refill_rate=0, clock=FakeClock())
        self.assertTrue(bucket.take().allowed)
        self.assertEqual(bucket.take().retry_after, math.inf)

    def test_denied_request_spends_nothing(self) -> None:
        bucket = TokenBucket(capacity=1, refill_rate=0, clock=FakeClock())
        bucket.take()
        for _ in range(5):
            self.assertFalse(bucket.take().allowed)
        self.assertAlmostEqual(bucket.tokens, 0.0)

    def test_start_empty(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(capacity=2, refill_rate=1, clock=clock, start_full=False)
        self.assertFalse(bucket.take().allowed)
        clock.advance(1)
        self.assertTrue(bucket.take().allowed)

    def test_clock_going_backwards_does_not_create_tokens(self) -> None:
        self.bucket.take(3)
        self.clock.advance(-5)
        self.assertAlmostEqual(self.bucket.tokens, 0.0)
        self.assertFalse(self.bucket.take().allowed)

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            TokenBucket(capacity=-1, refill_rate=1)
        with self.assertRaises(ValueError):
            TokenBucket(capacity=1, refill_rate=-0.5)
        bucket = TokenBucket(capacity=1, refill_rate=1, clock=FakeClock())
        with self.assertRaises(ValueError):
            bucket.take(0)
        with self.assertRaises(ValueError):
            bucket.take(-1)


class TestRateLimiter(unittest.TestCase):
    def test_keys_are_independent(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(capacity=1, refill_rate=0, clock=clock)
        self.assertTrue(limiter.check("a").allowed)
        self.assertTrue(limiter.check("b").allowed)
        self.assertFalse(limiter.check("a").allowed)
        self.assertFalse(limiter.check("b").allowed)

    def test_adding_a_key_moves_it_to_the_end(self) -> None:
        limiter = RateLimiter(capacity=1, refill_rate=0, clock=FakeClock(), max_keys=3)
        for key in "abc":
            limiter.check(key)
        limiter.check("a")
        self.assertEqual(limiter.keys(), ["b", "c", "a"])

    def test_lru_eviction_keeps_memory_bounded(self) -> None:
        limiter = RateLimiter(capacity=1, refill_rate=0, clock=FakeClock(), max_keys=2)
        for key in "abc":
            limiter.check(key)
        self.assertEqual(limiter.keys(), ["b", "c"])

    def test_evicted_key_returns_with_a_full_bucket(self) -> None:
        # Documented trade-off: cheap memory bound, weak fairness.
        limiter = RateLimiter(capacity=1, refill_rate=0, clock=FakeClock(), max_keys=1)
        self.assertTrue(limiter.check("a").allowed)
        self.assertFalse(limiter.check("a").allowed)
        limiter.check("b")  # evicts "a"
        self.assertTrue(limiter.check("a").allowed)

    def test_reset_and_snapshot(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(capacity=2, refill_rate=0, clock=clock)
        limiter.check("a")
        self.assertEqual(limiter.snapshot(), {"a": 1.0})
        self.assertTrue(limiter.reset("a"))
        self.assertEqual(limiter.snapshot(), {})
        self.assertFalse(limiter.reset("a"))
        self.assertTrue(limiter.check("a").allowed)

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            RateLimiter(capacity=1, refill_rate=1, max_keys=0)
        limiter = RateLimiter(capacity=1, refill_rate=1, clock=FakeClock())
        with self.assertRaises(ValueError):
            limiter.check("")
        with self.assertRaises(ValueError):
            limiter.check("a", cost=0)

    def test_default_clock_is_usable(self) -> None:
        limiter = RateLimiter(capacity=1, refill_rate=0)
        self.assertTrue(limiter.check("real-clock").allowed)
        self.assertFalse(limiter.check("real-clock").allowed)

    def test_threads_cannot_overspend_the_bucket(self) -> None:
        limiter = RateLimiter(capacity=10, refill_rate=0, clock=FakeClock())

        def hammer(_: int) -> bool:
            return limiter.check("shared").allowed

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(hammer, range(400)))

        self.assertEqual(sum(results), 10)


if __name__ == "__main__":
    unittest.main()
