"""Idempotency keys: run the side effect once, replay the answer forever.

A client POSTs a charge, the connection drops before the response arrives, and the
client retries — as it must. The server runs the side effect once, remembers the
answer under the client's key, and hands back the same bytes for every later request
with that key.

Three rules make the difference between "idempotent" and "idempotent most of the
time":

1. **Same key, different body is a conflict, not a replay.** A client bug that reuses
   ``key-123`` for a different amount must be refused before anything runs.
2. **A failed attempt does not poison the key.** If the handler raised, the side
   effect did not happen, so nothing is stored and the retry may execute.
3. **One execution at a time.** The retry storm that arrives while the first attempt
   is still running gets an in-flight answer, not a second charge.

The canonical fingerprint is what makes rule 1 work: the same JSON written in a
different order is the same request.

    >>> clock = FakeClock(0)
    >>> layer = IdempotencyLayer(MemoryStore(clock), clock, ttl_ms=1000)
    >>> calls = []

    >>> def charge(payload):
    ...     calls.append(payload["amount"])
    ...     return 201, '{"ok": true}'

    >>> first = layer.run("charge-1", {"amount": 10}, charge)
    >>> first.status, first.replayed, len(calls)
    (201, False, 1)

    >>> replay = layer.run("charge-1", {"amount": 10}, charge)
    >>> replay.body == first.body, replay.replayed, len(calls)
    (True, True, 1)

    >>> try:
    ...     layer.run("charge-1", {"amount": 999}, charge)
    ... except IdempotencyConflict as error:
    ...     print(error)
    charge-1 was first used for a different payload

Key order and whitespace do not change the request:

    >>> IdempotencyLayer.fingerprint({"a": 1, "b": [2, 3]}) == IdempotencyLayer.fingerprint({"b": [2, 3], "a": 1})
    True

A crash leaves the key usable — nothing was stored:

    >>> def broken(payload):
    ...     raise RuntimeError("primary is down")

    >>> try:
    ...     layer.run("charge-2", {"amount": 5}, broken)
    ... except RuntimeError as error:
    ...     print(f"failed: {error}")
    failed: primary is down

    >>> layer.run("charge-2", {"amount": 5}, charge).replayed
    False

After the TTL the record is gone, so the key behaves like a new one:

    >>> _ = clock.advance(1000)
    >>> layer.run("charge-1", {"amount": 10}, charge).replayed
    False
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol

__all__ = [
    "Clock",
    "FakeClock",
    "IdempotencyConflict",
    "IdempotencyError",
    "IdempotencyLayer",
    "MemoryStore",
    "Record",
    "RequestInFlight",
    "Result",
    "Store",
]

MAX_KEY_LENGTH = 255


class IdempotencyError(Exception):
    """Base class for every error this layer raises on purpose."""


class IdempotencyConflict(IdempotencyError):
    """The key already stored a *different* request. Nothing was executed."""


class RequestInFlight(IdempotencyError):
    """Another call with the same key is still running in this process."""


# ------------------------------------------------------------------ clock -- #
class Clock(Protocol):
    """Anything that can say what time it is, in epoch milliseconds."""

    def now_ms(self) -> int:  # pragma: no cover - protocol
        ...


@dataclass
class FakeClock:
    """A clock the tests move by hand; the layer never reads the wall clock."""

    current_ms: int = 0

    def now_ms(self) -> int:
        return self.current_ms

    def advance(self, delta_ms: int) -> int:
        if delta_ms < 0:
            raise ValueError("time does not run backwards in this fake")
        self.current_ms += delta_ms
        return self.current_ms


# ----------------------------------------------------------------- record -- #
@dataclass(frozen=True)
class Record:
    """What the store keeps per key: the fingerprint, the answer, and its deadline."""

    fingerprint: str
    status: int
    body: str
    created_ms: int
    expires_ms: int

    def is_expired(self, now_ms: int) -> bool:
        """Expiry is inclusive: at ``expires_ms`` the record is already gone."""
        return now_ms >= self.expires_ms


@dataclass(frozen=True)
class Result:
    """The answer a caller gets, plus whether it came from the store."""

    status: int
    body: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "body": self.body, "replayed": self.replayed}


# ------------------------------------------------------------------ store -- #
class Store(Protocol):
    """The pluggable part: memory in tests, Redis/Postgres in production."""

    def get(self, key: str) -> Record | None:  # pragma: no cover - protocol
        ...

    def put(self, key: str, record: Record) -> None:  # pragma: no cover - protocol
        ...

    def delete(self, key: str) -> bool:  # pragma: no cover - protocol
        ...


class MemoryStore:
    """A dict with expiry, good enough for tests and single-process services."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._records: dict[str, Record] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Record | None:
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return None
            if record.is_expired(self._clock.now_ms()):
                del self._records[key]
                return None
            return record

    def put(self, key: str, record: Record) -> None:
        with self._lock:
            self._records[key] = record

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._records.pop(key, None) is not None

    def purge(self) -> int:
        """Drop every expired record; returns how many were removed."""
        now = self._clock.now_ms()
        with self._lock:
            expired = [key for key, record in self._records.items() if record.is_expired(now)]
            for key in expired:
                del self._records[key]
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


# ------------------------------------------------------------------ layer -- #
Handler = Callable[[Any], tuple[int, Any]]


class IdempotencyLayer:
    """Run each distinct request once; replay the stored answer for repeats."""

    def __init__(self, store: Store, clock: Clock, ttl_ms: int = 24 * 60 * 60 * 1000) -> None:
        if ttl_ms <= 0:
            raise IdempotencyError("ttl_ms must be > 0")
        self.store = store
        self.clock = clock
        self.ttl_ms = ttl_ms
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self._stats = {"executed": 0, "replayed": 0, "conflicts": 0, "rejected": 0}

    # -- fingerprints ---------------------------------------------------- #
    @staticmethod
    def fingerprint(payload: Any) -> str:
        """A canonical hash: key order and whitespace must not change the request.

            >>> IdempotencyLayer.fingerprint({"b": 1, "a": 2}) == IdempotencyLayer.fingerprint(
            ...     {"a": 2, "b": 1}
            ... )
            True
            >>> len(IdempotencyLayer.fingerprint({"a": 1}))
            32
        """
        try:
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        except (TypeError, ValueError) as error:
            raise IdempotencyError(f"payload is not JSON-serialisable: {error}") from error
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    # -- stats ----------------------------------------------------------- #
    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    # -- the one public operation ---------------------------------------- #
    def run(self, key: str, payload: Any, handler: Handler) -> Result:
        """Execute ``handler`` at most once per key, replaying the stored answer.

        The claim is taken *before* the handler runs, so a re-entrant or concurrent
        call with the same key cannot execute a second side effect.
        """
        self._validate_key(key)
        fingerprint = self.fingerprint(payload)

        stored = self.store.get(key)
        if stored is not None:
            if stored.fingerprint != fingerprint:
                self._stats["conflicts"] += 1
                raise IdempotencyConflict(key + " was first used for a different payload")
            self._stats["replayed"] += 1
            return Result(status=stored.status, body=stored.body, replayed=True)

        with self._lock:
            if key in self._inflight:
                self._stats["rejected"] += 1
                raise RequestInFlight(f"{key} is already being processed")
            self._inflight.add(key)

        try:
            status, body = self._call(handler, payload)
            now = self.clock.now_ms()
            record = Record(
                fingerprint=fingerprint,
                status=int(status),
                body=self._body_text(body),
                created_ms=now,
                expires_ms=now + self.ttl_ms,
            )
            self.store.put(key, record)
            self._stats["executed"] += 1
            return Result(status=record.status, body=record.body, replayed=False)
        finally:
            # A failure must not poison the key: nothing is stored, and the next
            # caller is allowed to try again.
            with self._lock:
                self._inflight.discard(key)

    # -- helpers --------------------------------------------------------- #
    @staticmethod
    def _call(handler: Handler, payload: Any) -> tuple[int, Any]:
        result = handler(payload)
        if not isinstance(result, tuple) or len(result) != 2:
            raise IdempotencyError("handler must return (status, body)")
        status, body = result
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise IdempotencyError(f"handler returned an invalid status: {status!r}")
        return status, body

    @staticmethod
    def _body_text(body: Any) -> str:
        if isinstance(body, str):
            return body
        try:
            return json.dumps(body, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise IdempotencyError(f"handler body is not JSON-serialisable: {error}") from error

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str):
            raise IdempotencyError("the idempotency key must be a string")
        if not key.strip():
            raise IdempotencyError("the idempotency key must not be empty")
        if len(key) > MAX_KEY_LENGTH:
            raise IdempotencyError(f"the idempotency key is longer than {MAX_KEY_LENGTH} characters")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"IdempotencyLayer(ttl_ms={self.ttl_ms}, stats={self.stats})"
