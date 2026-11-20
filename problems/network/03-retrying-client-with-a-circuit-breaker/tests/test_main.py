"""Tests for backoff, retry safety, breaker transitions, and the client wiring."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from solution import cli
from solution.main import (
    BreakerState,
    CircuitBreaker,
    CircuitOpen,
    FakeClock,
    PermanentError,
    RetriesExhausted,
    RetryPolicy,
    RetryingClient,
    TransientError,
    flaky_callable,
)


def make(**policy_kwargs: object) -> tuple[FakeClock, CircuitBreaker, RetryingClient, list[float]]:
    """A client whose breaker will not interfere unless a test wants it to."""
    clock = FakeClock()
    breaker = CircuitBreaker(clock, failure_threshold=1000, reset_timeout=30.0)
    sleeps: list[float] = []
    client = RetryingClient(
        breaker,
        policy=RetryPolicy(**policy_kwargs),  # type: ignore[arg-type]
        clock=clock,
        sleeper=sleeps.append,
    )
    return clock, breaker, client, sleeps


def always_down() -> str:
    raise TransientError("connection refused")


class TestPolicy(unittest.TestCase):
    def test_exponential_schedule(self) -> None:
        policy = RetryPolicy(attempts=4, base_delay=0.1, multiplier=2.0, max_delay=10.0)
        self.assertEqual(policy.delays(), [0.1, 0.2, 0.4])

    def test_delay_is_capped(self) -> None:
        policy = RetryPolicy(attempts=6, base_delay=1.0, multiplier=10.0, max_delay=5.0)
        self.assertEqual(policy.delays(), [1.0, 5.0, 5.0, 5.0, 5.0])

    def test_jitter_scales_the_delay(self) -> None:
        policy = RetryPolicy(attempts=3, base_delay=2.0, jitter=lambda: 0.5)
        self.assertEqual(policy.delays(), [1.0, 2.0])

    def test_single_attempt_sleeps_once_never(self) -> None:
        self.assertEqual(RetryPolicy(attempts=1).delays(), [])

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            RetryPolicy(attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(base_delay=-1)
        with self.assertRaises(ValueError):
            RetryPolicy(multiplier=0.5)
        with self.assertRaises(ValueError):
            RetryPolicy().delay_for(0)

    def test_policy_is_immutable(self) -> None:
        policy = RetryPolicy()
        with self.assertRaises(Exception):
            policy.attempts = 9  # type: ignore[misc]


class TestBreaker(unittest.TestCase):
    def test_starts_closed_and_allows_calls(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=2)
        self.assertIs(breaker.state, BreakerState.CLOSED)
        self.assertTrue(breaker.allow())
        self.assertEqual(breaker.retry_in(), 0.0)

    def test_opens_after_the_threshold_of_consecutive_failures(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=3, reset_timeout=30.0)
        breaker.record_failure()
        breaker.record_failure()
        self.assertIs(breaker.state, BreakerState.CLOSED)
        breaker.record_failure()
        self.assertIs(breaker.state, BreakerState.OPEN)
        self.assertFalse(breaker.allow())
        self.assertEqual(breaker.retry_in(), 30.0)

    def test_success_resets_the_failure_count(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=2)
        breaker.record_failure()
        breaker.record_success()
        self.assertEqual(breaker.failures, 0)
        breaker.record_failure()
        self.assertIs(breaker.state, BreakerState.CLOSED, "one failure after a success is not enough")

    def test_cool_down_promotes_to_half_open_and_allows_one_probe(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=1, reset_timeout=10.0)
        breaker.record_failure()
        clock.advance(9.9)
        self.assertIs(breaker.state, BreakerState.OPEN)
        clock.advance(0.1)
        self.assertIs(breaker.state, BreakerState.HALF_OPEN)
        self.assertTrue(breaker.allow())
        self.assertFalse(breaker.allow(), "only one trial call is allowed")

    def test_probe_success_closes_the_breaker(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=1, reset_timeout=5.0)
        breaker.record_failure()
        clock.advance(5.0)
        self.assertTrue(breaker.allow())
        breaker.record_success()
        self.assertIs(breaker.state, BreakerState.CLOSED)
        self.assertTrue(breaker.allow())

    def test_probe_failure_reopens_the_breaker(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=1, reset_timeout=5.0)
        breaker.record_failure()
        clock.advance(5.0)
        breaker.allow()
        breaker.record_failure()
        self.assertIs(breaker.state, BreakerState.OPEN)
        self.assertFalse(breaker.allow())
        self.assertEqual(breaker.retry_in(), 5.0, "the cool-down restarts")

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            CircuitBreaker(FakeClock(), failure_threshold=0)
        with self.assertRaises(ValueError):
            CircuitBreaker(FakeClock(), reset_timeout=-1)


class TestClient(unittest.TestCase):
    def test_returns_the_first_success_without_sleeping(self) -> None:
        _, _, client, sleeps = make()
        self.assertEqual(client.call(lambda: "value"), "value")
        self.assertEqual(sleeps, [])
        self.assertEqual(client.stats["attempts"], 1)
        self.assertEqual(client.stats["succeeded"], 1)

    def test_retries_until_success_with_backoff(self) -> None:
        _, _, client, sleeps = make(attempts=4, base_delay=0.5)
        self.assertEqual(client.call(flaky_callable(2)), "ok")
        self.assertEqual(sleeps, [0.5, 1.0])
        self.assertEqual(client.stats["retries"], 2)
        self.assertEqual(client.stats["slept"], 1.5)

    def test_exhaustion_carries_the_last_error_and_attempt_count(self) -> None:
        _, _, client, _ = make(attempts=3)
        with self.assertRaises(RetriesExhausted) as raised:
            client.call(always_down)
        self.assertEqual(raised.exception.attempts, 3)
        self.assertIsInstance(raised.exception.last_error, TransientError)
        self.assertIn("gave up after 3 attempts", str(raised.exception))

    def test_attempt_count_is_per_call_not_cumulative(self) -> None:
        _, _, client, _ = make(attempts=1)
        for _ in range(2):
            with self.assertRaises(RetriesExhausted) as raised:
                client.call(always_down)
            self.assertEqual(raised.exception.attempts, 1)

    def test_permanent_errors_are_not_retried(self) -> None:
        _, _, client, sleeps = make(attempts=4)

        def broken() -> str:
            raise PermanentError("400 bad request")

        with self.assertRaises(PermanentError):
            client.call(broken)
        self.assertEqual(sleeps, [])
        self.assertEqual(client.stats["attempts"], 1)

    def test_a_sent_request_is_not_retried_when_the_call_is_not_idempotent(self) -> None:
        _, _, client, sleeps = make(attempts=4)

        def charged_but_timeout() -> str:
            raise TransientError("read timeout after send", sent=True)

        with self.assertRaises(RetriesExhausted) as raised:
            client.call(charged_but_timeout, retry_safe=False)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(sleeps, [])

    def test_an_unsent_request_may_be_retried_even_when_not_idempotent(self) -> None:
        _, _, client, sleeps = make(attempts=3)

        def refused() -> str:
            raise TransientError("connection refused", sent=False)

        with self.assertRaises(RetriesExhausted) as raised:
            client.call(refused, retry_safe=False)
        self.assertEqual(raised.exception.attempts, 3)
        self.assertEqual(len(sleeps), 2)

    def test_an_open_breaker_short_circuits_without_calling(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=1, reset_timeout=20.0)
        breaker.record_failure()
        client = RetryingClient(breaker, policy=RetryPolicy(attempts=2), clock=clock)

        called = {"count": 0}

        def counted() -> str:
            called["count"] += 1
            return "never"

        with self.assertRaises(CircuitOpen) as raised:
            client.call(counted)
        self.assertEqual(called["count"], 0, "the network must not be touched")
        self.assertEqual(raised.exception.retry_in, 20.0)
        self.assertEqual(client.stats["short_circuited"], 1)

    def test_failures_trip_the_breaker_while_retrying(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=2, reset_timeout=5.0)
        client = RetryingClient(breaker, policy=RetryPolicy(attempts=3), clock=clock)
        with self.assertRaises(RetriesExhausted):
            client.call(always_down)
        self.assertIs(breaker.state, BreakerState.OPEN)
        with self.assertRaises(CircuitOpen):
            client.call(always_down)

    def test_recovery_after_the_cool_down(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(clock, failure_threshold=1, reset_timeout=10.0)
        client = RetryingClient(breaker, policy=RetryPolicy(attempts=1), clock=clock)

        with self.assertRaises(RetriesExhausted):
            client.call(always_down)
        clock.advance(10.0)
        self.assertEqual(client.call(lambda: "back"), "back")
        self.assertIs(breaker.state, BreakerState.CLOSED)
        self.assertEqual(client.stats["short_circuited"], 0)

    def test_stats_snapshot_is_a_copy(self) -> None:
        _, _, client, _ = make()
        snapshot = client.stats
        snapshot["calls"] = 99
        self.assertEqual(client.stats["calls"], 0)


class TestCli(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        return code, buffer.getvalue()

    def test_flaky_dependency_recovers(self) -> None:
        code, output = self.run_cli(["--failures", "2", "--attempts", "4", "--calls", "1"])
        self.assertEqual(code, 0)
        self.assertIn("ok: response", output)
        self.assertIn("[0.1, 0.2]", output)

    def test_outage_opens_the_breaker_for_the_next_call(self) -> None:
        code, output = self.run_cli(
            ["--failures", "99", "--attempts", "2", "--threshold", "1", "--calls", "2"]
        )
        self.assertEqual(code, 1)
        self.assertIn("exhausted", output)
        self.assertIn("short-circuited", output)
        self.assertIn("breaker: open", output)

    def test_json_report(self) -> None:
        code, output = self.run_cli(["--failures", "1", "--attempts", "2", "--json", "--calls", "1"])
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report["outcomes"], ["ok: response"])
        self.assertEqual(report["breaker"], "closed")
        self.assertEqual(report["stats"]["retries"], 1)

    def test_bad_arguments_exit_two(self) -> None:
        self.assertEqual(self.run_cli(["--attempts", "0"])[0], 2)
        self.assertEqual(self.run_cli(["--calls", "0"])[0], 2)
        self.assertEqual(self.run_cli(["--failures", "-1"])[0], 2)


if __name__ == "__main__":
    unittest.main()
