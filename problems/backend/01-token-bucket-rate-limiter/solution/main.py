"""A per-key token bucket rate limiter that is safe to call from many threads.

Why the code looks like this
---------------------------
* **The clock is injected.** ``time.sleep`` in a test means a flaky test, so every
  bucket reads time through a callable. Production passes ``time.monotonic``
  (immune to wall-clock jumps); tests pass a fake that they advance by hand.
* **Rejected requests never spend tokens.** A denied request must not push the
  client further away from its next allowed request.
* **One lock guards the dict and the buckets.** Correctness first; the docstring
  notes where to shard when the lock becomes hot.
* **The key map is bounded** (LRU) so a hostile client cannot grow memory forever.
  The trade-off — an evicted key returns with a full bucket — is documented in
  ``problem.md`` instead of hidden.

Example
-------
>>> fake = [0.0]
>>> bucket = TokenBucket(capacity=2, refill_rate=1, clock=lambda: fake[0])
>>> [bucket.take().allowed for _ in range(3)]
[True, True, False]
>>> fake[0] = 1.0
>>> bucket.take().allowed
True
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Iterable

DEFAULT_MAX_KEYS = 10_000

Clock = Callable[[], float]


@dataclass(frozen=True)
class Decision:
    """The answer handed back to the caller (and to the `Retry-After` header)."""

    allowed: bool
    remaining: float
    retry_after: float


class TokenBucket:
    """A single bucket. Not thread-safe by design — `RateLimiter` owns the lock."""

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        *,
        clock: Clock = time.monotonic,
        start_full: bool = True,
    ) -> None:
        if capacity < 0:
            raise ValueError(f"capacity must be >= 0, got {capacity}")
        if refill_rate < 0:
            raise ValueError(f"refill_rate must be >= 0, got {refill_rate}")

        self._capacity = float(capacity)
        self._refill_rate = float(refill_rate)
        self._clock = clock
        self._tokens = self._capacity if start_full else 0.0
        self._updated_at = clock()

    @property
    def capacity(self) -> float:
        return self._capacity

    @property
    def tokens(self) -> float:
        """Tokens available right now (after accounting for elapsed refill)."""
        self._refill()
        return self._tokens

    def take(self, cost: float = 1.0) -> Decision:
        """Try to spend `cost` tokens; never spends anything when it denies."""
        if cost <= 0:
            raise ValueError(f"cost must be > 0, got {cost}")

        self._refill()
        if self._tokens >= cost:
            self._tokens -= cost
            return Decision(allowed=True, remaining=self._tokens, retry_after=0.0)

        missing = cost - self._tokens
        retry_after = missing / self._refill_rate if self._refill_rate > 0 else float("inf")
        return Decision(allowed=False, remaining=self._tokens, retry_after=retry_after)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated_at
        if elapsed <= 0:  # clock went backwards or nothing to do
            return
        self._updated_at = now
        if self._refill_rate:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"TokenBucket(capacity={self._capacity}, refill_rate={self._refill_rate}, "
            f"tokens={self._tokens:.3f})"
        )


class RateLimiter:
    """Independent token bucket per key, bounded to `max_keys` via LRU eviction."""

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        *,
        clock: Clock = time.monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        if capacity < 0:
            raise ValueError(f"capacity must be >= 0, got {capacity}")
        if refill_rate < 0:
            raise ValueError(f"refill_rate must be >= 0, got {refill_rate}")
        if max_keys < 1:
            raise ValueError(f"max_keys must be >= 1, got {max_keys}")

        self._capacity = float(capacity)
        self._refill_rate = float(refill_rate)
        self._clock = clock
        self._max_keys = int(max_keys)
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str, cost: float = 1.0) -> Decision:
        """Spend `cost` tokens for `key`, creating its bucket on first sight."""
        if not key:
            raise ValueError("key must be a non-empty string")
        if cost <= 0:
            raise ValueError(f"cost must be > 0, got {cost}")

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(
                    self._capacity, self._refill_rate, clock=self._clock, start_full=True
                )
                self._buckets[key] = bucket
                self._evict_if_needed()
            else:
                self._buckets.move_to_end(key)  # mark as recently used
            return bucket.take(cost)

    def reset(self, key: str) -> bool:
        """Forget a key's bucket (e.g. after a password reset / billing change)."""
        with self._lock:
            return self._buckets.pop(key, None) is not None

    def keys(self) -> list[str]:
        """Tracked keys, least recently used first (useful for metrics and tests)."""
        with self._lock:
            return list(self._buckets)

    def snapshot(self) -> dict[str, float]:
        """Current token count per key — what you expose to a dashboard."""
        with self._lock:
            return {key: bucket.tokens for key, bucket in self._buckets.items()}

    def _evict_if_needed(self) -> None:
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"RateLimiter(capacity={self._capacity}, refill_rate={self._refill_rate}, "
            f"keys={len(self._buckets)})"
        )
