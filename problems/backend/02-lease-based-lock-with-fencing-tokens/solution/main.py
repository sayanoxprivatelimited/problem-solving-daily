"""Lease-based mutual exclusion, made safe by fencing tokens.

A lease on its own is not a lock: the holder can stall past its expiry, and when it
comes back the resource may already belong to somebody else. What makes the scheme
safe is the number attached to every grant — a *fencing token* that only ever goes
up — plus a guarded resource that refuses writes carrying an older number.

The clock is injected (`Clock`), so nothing here sleeps and every test can jump
forward in time instead of waiting for it.

Example
-------
>>> clock = FakeClock(start=0.0)
>>> lock = LeaseLock(clock, lease=10.0)
>>> first = lock.acquire("worker-a")
>>> first.fence, first.expires_at
(1, 10.0)
>>> lock.renew("worker-a", first)
True
>>> _ = clock.advance(11.0)      # worker-a stalls past its lease
>>> lock.renew("worker-a", first)
False
>>> second = lock.acquire("worker-b")
>>> second.fence
2
>>> guard = FenceGuard()
>>> guard.write(second, "invoice-42 v2")
2
>>> try:                                  # the stalled worker wakes up
...     guard.write(first, "invoice-42 v1")
... except StaleTokenError as error:
...     str(error)
'fence 1 <= highest applied 2'
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol


class Clock(Protocol):
    """Anything with a monotonic `now()` in seconds."""

    def now(self) -> float: ...


class FakeClock:
    """A clock that only moves when a test says so."""

    def __init__(self, start: float = 0.0) -> None:
        self._time = float(start)
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._time

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("time does not run backwards")
        with self._lock:
            self._time += float(seconds)
            return self._time


class LockError(RuntimeError):
    """Base class for lease-lock errors."""


class LockHeld(LockError):
    """A live lease belongs to another owner."""

    def __init__(self, holder: FenceToken) -> None:
        self.holder = holder
        super().__init__(f"lease held by {holder.owner!r} until {holder.expires_at}")


class StaleTokenError(LockError):
    """A write arrived carrying a fence that has already been superseded."""

    def __init__(self, fence: int, highest: int) -> None:
        self.fence = fence
        self.highest = highest
        super().__init__(f"fence {fence} <= highest applied {highest}")


@dataclass(frozen=True)
class FenceToken:
    """What a holder gets back from `acquire`; also what a writer must present."""

    owner: str
    fence: int
    expires_at: float

    def is_expired(self, clock: Clock) -> bool:
        """Expired exactly at `expires_at` — a lease never covers its final instant."""
        return clock.now() >= self.expires_at

    def remaining(self, clock: Clock) -> float:
        return max(0.0, self.expires_at - clock.now())


class LeaseLock:
    """Hands out at most one live lease, and a monotonically increasing fence."""

    def __init__(self, clock: Clock, lease: float, name: str = "lock") -> None:
        if lease <= 0:
            raise ValueError("lease must be positive")
        self._clock = clock
        self._lease = float(lease)
        self._name = name
        self._mutex = threading.Lock()
        self._holder: FenceToken | None = None
        self._fence = 0
        self._grants = 0

    # -- helpers ---------------------------------------------------------
    def _live_holder(self) -> FenceToken | None:
        if self._holder is not None and self._holder.is_expired(self._clock):
            self._holder = None  # expired leases free themselves
        return self._holder

    # -- API -------------------------------------------------------------
    def acquire(self, owner: str) -> FenceToken:
        """Grant the lease to `owner`, or raise `LockHeld` if somebody live holds it."""
        if not owner:
            raise ValueError("owner must be a non-empty string")
        with self._mutex:
            holder = self._live_holder()
            if holder is not None:
                raise LockHeld(holder)
            self._fence += 1
            self._grants += 1
            self._holder = FenceToken(owner, self._fence, self._clock.now() + self._lease)
            return self._holder

    def try_acquire(self, owner: str) -> FenceToken | None:
        """`acquire`, but `None` instead of an exception when the lease is busy."""
        try:
            return self.acquire(owner)
        except LockHeld:
            return None

    def _owns(self, owner: str, token: FenceToken) -> bool:
        """True when `token` still names the live holder.

        Identity is the *fence*, not the object: renewing replaces the deadline and
        returns nothing, so a holder that keeps a single token around can keep
        renewing it. A token from before a takeover has an older fence and fails.
        """
        holder = self._live_holder()
        return holder is not None and holder.fence == token.fence and holder.owner == owner

    def renew(self, owner: str, token: FenceToken) -> bool:
        """Extend the lease from now. False for a stale, foreign or expired token."""
        with self._mutex:
            if not self._owns(owner, token):
                return False
            self._holder = FenceToken(owner, token.fence, self._clock.now() + self._lease)
            return True

    def release(self, owner: str, token: FenceToken) -> bool:
        """Drop the lease if this fence still owns it. Fence values are never reused."""
        with self._mutex:
            if not self._owns(owner, token):
                return False
            self._holder = None
            return True

    @property
    def holder(self) -> FenceToken | None:
        """The live holder, or None (an expired lease counts as free)."""
        with self._mutex:
            return self._live_holder()

    @property
    def fence(self) -> int:
        """Highest fence ever granted — the number a resource should compare against."""
        with self._mutex:
            return self._fence

    def snapshot(self) -> dict[str, object]:
        with self._mutex:
            holder = self._live_holder()
            return {
                "name": self._name,
                "holder": holder.owner if holder else None,
                "fence": self._fence,
                "grants": self._grants,
                "lease": self._lease,
                "now": self._clock.now(),
            }


class FenceGuard:
    """A resource that applies writes only in increasing fence order.

    This is the half of the pattern people forget: a fencing token is useless unless
    something downstream compares it. The guard keeps the highest fence it has
    applied and rejects anything that is not strictly newer.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._highest = 0
        self._writes: list[tuple[int, object]] = []

    def write(self, token: FenceToken, value: object) -> int:
        """Apply `value` under `token`. Returns the fence that was applied."""
        with self._mutex:
            if token.fence <= self._highest:
                raise StaleTokenError(token.fence, self._highest)
            self._highest = token.fence
            self._writes.append((token.fence, value))
            return token.fence

    def try_write(self, token: FenceToken, value: object) -> bool:
        try:
            self.write(token, value)
        except StaleTokenError:
            return False
        return True

    @property
    def highest(self) -> int:
        with self._mutex:
            return self._highest

    @property
    def writes(self) -> tuple[tuple[int, object], ...]:
        with self._mutex:
            return tuple(self._writes)

    @property
    def value(self) -> object | None:
        with self._mutex:
            return self._writes[-1][1] if self._writes else None
