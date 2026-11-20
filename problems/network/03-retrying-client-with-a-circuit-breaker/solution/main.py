"""Retries with jitter, plus a circuit breaker that fails fast during an outage.

The two halves answer different questions:

* `RetryPolicy` answers "how long do I wait before trying again, and is trying again
  even safe?" — a sent `POST` is not safely retryable.
* `CircuitBreaker` answers "should this call touch the network at all?" — after
  enough consecutive failures the answer is no, until a single probe says otherwise.

Time and sleeping are injected, so nothing here blocks and every scenario is
deterministic.

Example
-------
>>> clock = FakeClock()
>>> sleeps: list[float] = []
>>> breaker = CircuitBreaker(clock, failure_threshold=3, reset_timeout=30.0)
>>> client = RetryingClient(breaker, clock=clock, sleeper=sleeps.append,
...                         policy=RetryPolicy(attempts=3, base_delay=0.5))
>>> flaky = flaky_callable(2)          # fails twice, then succeeds
>>> client.call(flaky)
'ok'
>>> sleeps
[0.5, 1.0]
>>> breaker.state.value
'closed'

An outage opens the breaker, and the next call never reaches the network:

>>> outage = RetryingClient(breaker, clock=clock, sleeper=sleeps.append)
>>> def down() -> str:
...     raise TransientError("connection refused")
>>> try:
...     outage.call(down)
... except RetriesExhausted as error:
...     str(error)
'gave up after 4 attempts: connection refused'
>>> breaker.state.value
'open'
>>> try:
...     outage.call(down)
... except CircuitOpen as error:
...     f"rejected locally, retry in {error.retry_in}s"
'rejected locally, retry in 30.0s'

After the reset timeout exactly one probe is allowed:

>>> _ = clock.advance(30.0)
>>> breaker.state.value
'half_open'
>>> breaker.allow()                 # the single probe
True
>>> breaker.allow()                 # everyone else waits
False
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, TypeVar

T = TypeVar("T")


class Clock(Protocol):
    def now(self) -> float: ...


class FakeClock:
    """A clock that only moves when a test says so."""

    def __init__(self, start: float = 0.0) -> None:
        self._time = float(start)
        self._mutex = threading.Lock()

    def now(self) -> float:
        with self._mutex:
            return self._time

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("time does not run backwards")
        with self._mutex:
            self._time += float(seconds)
            return self._time


# --------------------------------------------------------------------- errors --
class TransientError(Exception):
    """A failure worth retrying — but only if the request may be repeated safely.

    `sent` is the important bit: a timeout while establishing the connection never
    reached the server (`sent=False`), while a timeout after sending might have been
    applied (`sent=True`).
    """

    def __init__(self, message: str = "transient failure", sent: bool = True) -> None:
        self.sent = sent
        super().__init__(message)


class PermanentError(Exception):
    """A failure that retrying cannot fix: a 400, a schema mismatch, a bug."""


class CircuitOpen(RuntimeError):
    """The breaker is open, so the call was rejected without touching the network."""

    def __init__(self, retry_in: float) -> None:
        self.retry_in = retry_in
        super().__init__(f"circuit open, retry in {retry_in:.1f}s")


class RetriesExhausted(RuntimeError):
    """Every attempt failed; `last_error` says how and `attempts` how many."""

    def __init__(self, last_error: BaseException, attempts: int) -> None:
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"gave up after {attempts} attempts: {last_error}")


# --------------------------------------------------------------------- policy --
def no_jitter() -> float:
    """Default jitter: none, so a test can predict the schedule exactly."""
    return 1.0


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to try, and how long to wait in between."""

    attempts: int = 4
    base_delay: float = 0.1
    multiplier: float = 2.0
    max_delay: float = 5.0
    jitter: Callable[[], float] = no_jitter

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be >= 0")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait after failed attempt number `attempt` (1-based)."""
        if attempt < 1:
            raise ValueError("attempt numbers start at 1")
        raw = self.base_delay * (self.multiplier ** (attempt - 1))
        return min(raw, self.max_delay) * self.jitter()

    def delays(self) -> list[float]:
        """The full sleep schedule for `attempts` attempts."""
        return [self.delay_for(attempt) for attempt in range(1, self.attempts)]


# -------------------------------------------------------------------- breaker --
class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Consecutive-failure breaker with one trial call after the cool-down."""

    clock: Clock
    failure_threshold: int = 3
    reset_timeout: float = 30.0
    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _probe_taken: bool = field(default=False, init=False)
    _mutex: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.reset_timeout < 0:
            raise ValueError("reset_timeout must be >= 0")

    # -- state -----------------------------------------------------------
    @property
    def state(self) -> BreakerState:
        """Current state, promoting OPEN to HALF_OPEN once the cool-down is over."""
        with self._mutex:
            self._maybe_half_open()
            return self._state

    @property
    def failures(self) -> int:
        with self._mutex:
            return self._failures

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and self._opened_at is not None:
            if self.clock.now() - self._opened_at >= self.reset_timeout:
                self._state = BreakerState.HALF_OPEN
                self._probe_taken = False

    def retry_in(self) -> float:
        """Seconds until a probe is allowed (0 when the breaker is not open)."""
        with self._mutex:
            if self._state is BreakerState.CLOSED:
                return 0.0
            self._maybe_half_open()
            if self._state is BreakerState.HALF_OPEN:
                return 0.0
            assert self._opened_at is not None
            return max(0.0, self.reset_timeout - (self.clock.now() - self._opened_at))

    # -- gate ------------------------------------------------------------
    def allow(self) -> bool:
        """True if a call may proceed. In HALF_OPEN only the single probe passes."""
        with self._mutex:
            self._maybe_half_open()
            if self._state is BreakerState.CLOSED:
                return True
            if self._state is BreakerState.OPEN:
                return False
            if self._probe_taken:
                return False
            self._probe_taken = True
            return True

    def record_success(self) -> None:
        with self._mutex:
            self._state = BreakerState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._probe_taken = False

    def record_failure(self) -> None:
        with self._mutex:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN:
                self._trip()
                return
            if self._state is BreakerState.CLOSED and self._failures >= self.failure_threshold:
                self._trip()

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self.clock.now()
        self._probe_taken = False


# --------------------------------------------------------------------- client --
class RetryingClient:
    """Calls a function through a policy and a breaker, recording what happened."""

    def __init__(
        self,
        breaker: CircuitBreaker,
        policy: RetryPolicy | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.breaker = breaker
        self.policy = policy or RetryPolicy()
        self.clock = clock or breaker.clock
        self.sleeper = sleeper or (lambda seconds: None)
        self._stats = {
            "calls": 0,
            "attempts": 0,
            "retries": 0,
            "slept": 0.0,
            "short_circuited": 0,
            "succeeded": 0,
            "failed": 0,
        }

    @property
    def stats(self) -> dict[str, float]:
        return dict(self._stats)

    def call(self, fn: Callable[[], T], *, retry_safe: bool = True) -> T:
        """Run `fn`, retrying per the policy, and gating every attempt on the breaker."""
        self._stats["calls"] += 1

        if not self.breaker.allow():
            self._stats["short_circuited"] += 1
            raise CircuitOpen(self.breaker.retry_in())

        last_error: BaseException | None = None
        made = 0

        for attempt in range(1, self.policy.attempts + 1):
            made = attempt
            self._stats["attempts"] += 1
            try:
                value = fn()
            except PermanentError:
                self.breaker.record_failure()
                self._stats["failed"] += 1
                raise
            except TransientError as error:
                last_error = error
                self.breaker.record_failure()
                self._stats["failed"] += 1

                if attempt == self.policy.attempts:
                    break
                # A request that already reached the server must not be repeated
                # unless the caller promised the operation is idempotent.
                if error.sent and not retry_safe:
                    break

                delay = self.policy.delay_for(attempt)
                self._stats["retries"] += 1
                self._stats["slept"] += delay
                self.sleeper(delay)
            else:
                self.breaker.record_success()
                self._stats["succeeded"] += 1
                return value

        assert last_error is not None
        raise RetriesExhausted(last_error, made)


def flaky_callable(failures: int) -> Callable[[], str]:
    """A function that raises `TransientError` `failures` times, then returns 'ok'."""
    remaining = {"left": failures}

    def call() -> str:
        if remaining["left"] > 0:
            remaining["left"] -= 1
            raise TransientError("upstream hiccup")
        return "ok"

    return call
