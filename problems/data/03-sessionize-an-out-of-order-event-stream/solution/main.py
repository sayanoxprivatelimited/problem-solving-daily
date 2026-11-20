"""Session windows over an out-of-order event stream, closed by a watermark.

Two things make this non-trivial, and both are about *time*, not about sorting:

* an event can arrive late and **bridge** two open sessions, which must then merge;
* a session may only be emitted once nothing can extend it any more, which is what
  the watermark (`max_ts_seen - allowed_lateness`) is for.

Everything is integer milliseconds taken from the events, so the output is
deterministic and no clock is ever read.

Example
-------
>>> s = Sessionizer(gap_ms=5000)
>>> _ = s.add(Event(1000, "u1"))          # nothing can close yet
>>> _ = s.add(Event(3000, "u1"))
>>> _ = s.add(Event(4000, "u1"))          # all within 5s of the previous event
>>> emitted = s.add(Event(20000, "u1"))   # 16s later: the first session is done
>>> [(session.start, session.end, session.count) for session in emitted]
[(1000, 4000, 3)]
>>> [(session.start, session.end, session.count) for session in s.flush()]
[(20000, 20000, 1)]

A late event that lands between two open sessions merges them into one:

>>> bridged = sessions_from(
...     [Event(0, "u"), Event(6000, "u"), Event(3000, "u")],
...     gap_ms=3000,
...     allowed_lateness_ms=4000,
... )
>>> [(session.start, session.end, session.count) for session in bridged]
[(0, 6000, 3)]

Without the lateness budget the first session is closed before the late event
arrives, so it stays two sessions — and the late event is dropped:

>>> out_of_luck = sessions_from(
...     [Event(0, "u"), Event(6000, "u"), Event(3000, "u")], gap_ms=3000
... )
>>> [(session.start, session.end) for session in out_of_luck]
[(0, 0), (6000, 6000)]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Event:
    """One activity record. `arrival` is filled in by the sessionizer."""

    ts: int
    key: str
    value: str = ""
    arrival: int = 0

    def __post_init__(self) -> None:
        if self.ts < 0:
            raise ValueError(f"ts must be >= 0, got {self.ts}")
        if not self.key:
            raise ValueError("key must be a non-empty string")


@dataclass(frozen=True)
class Session:
    """A closed session: immutable, and its events are in (ts, arrival) order."""

    key: str
    start: int
    end: int
    events: tuple[Event, ...]

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def duration(self) -> int:
        return self.end - self.start

    def covers(self, ts: int) -> bool:
        return self.start <= ts <= self.end


@dataclass
class _Open:
    """Mutable session under construction."""

    key: str
    start: int
    end: int
    events: list[Event] = field(default_factory=list)

    def close(self) -> Session:
        ordered = tuple(sorted(self.events, key=lambda event: (event.ts, event.arrival)))
        return Session(self.key, self.start, self.end, ordered)


class Sessionizer:
    """Groups events into sessions, emitting each one exactly once.

    Parameters
    ----------
    gap_ms:
        Maximum distance between two neighbouring events of the same session.
    allowed_lateness_ms:
        How far behind the newest event an event may still arrive and be accepted.
    """

    def __init__(self, gap_ms: int, allowed_lateness_ms: int = 0) -> None:
        if gap_ms < 0:
            raise ValueError("gap_ms must be >= 0")
        if allowed_lateness_ms < 0:
            raise ValueError("allowed_lateness_ms must be >= 0")
        self.gap_ms = gap_ms
        self.allowed_lateness_ms = allowed_lateness_ms
        self._open: dict[str, list[_Open]] = {}
        self._max_ts = 0
        self._arrivals = 0
        self._events = 0
        self._late_dropped = 0
        self._emitted = 0

    # -- introspection ---------------------------------------------------
    @property
    def watermark(self) -> int:
        """Nothing older than this can be accepted any more."""
        return max(0, self._max_ts - self.allowed_lateness_ms)

    @property
    def open_sessions(self) -> int:
        return sum(len(sessions) for sessions in self._open.values())

    @property
    def stats(self) -> dict[str, int]:
        return {
            "events": self._events,
            "late_dropped": self._late_dropped,
            "sessions_emitted": self._emitted,
            "open": self.open_sessions,
            "watermark": self.watermark,
        }

    # -- ingest ----------------------------------------------------------
    def add(self, event: Event) -> list[Session]:
        """Absorb one event; return the sessions this event closed."""
        stamped = event if event.arrival else Event(event.ts, event.key, event.value, self._arrivals)
        self._arrivals += 1

        if stamped.ts < self.watermark:
            self._late_dropped += 1
            return []

        self._events += 1
        self._max_ts = max(self._max_ts, stamped.ts)
        self._absorb(stamped)
        return self._close_finished()

    def add_all(self, events: Iterable[Event]) -> list[Session]:
        """Feed a batch, collecting everything it closed."""
        emitted: list[Session] = []
        for event in events:
            emitted.extend(self.add(event))
        return emitted

    def _absorb(self, event: Event) -> None:
        """Insert the event, merging every session it now touches."""
        sessions = self._open.setdefault(event.key, [])

        touched = [
            session
            for session in sessions
            if not (event.ts < session.start - self.gap_ms or event.ts > session.end + self.gap_ms)
        ]

        if not touched:
            sessions.append(_Open(event.key, event.ts, event.ts, [event]))
            sessions.sort(key=lambda session: session.start)
            return

        merged = _Open(
            event.key,
            start=min([event.ts, *(session.start for session in touched)]),
            end=max([event.ts, *(session.end for session in touched)]),
            events=[event, *(item for session in touched for item in session.events)],
        )
        self._open[event.key] = [
            session for session in sessions if not any(session is item for item in touched)
        ]
        self._open[event.key].append(merged)
        self._open[event.key].sort(key=lambda session: session.start)

    def _close_finished(self) -> list[Session]:
        """Emit every session the watermark can no longer extend."""
        watermark = self.watermark
        emitted: list[Session] = []

        for key in list(self._open):
            remaining = []
            for session in self._open[key]:
                # The watermark is exclusive: events at or after it may still arrive,
                # so a session is safe only once its reachable window is strictly behind it.
                if session.end + self.gap_ms < watermark:
                    emitted.append(session.close())
                else:
                    remaining.append(session)
            if remaining:
                self._open[key] = remaining
            else:
                del self._open[key]

        emitted.sort(key=lambda session: (session.key, session.start))
        self._emitted += len(emitted)
        return emitted

    def flush(self) -> list[Session]:
        """Close everything still open. After this the sessionizer is empty."""
        emitted = [session.close() for sessions in self._open.values() for session in sessions]
        emitted.sort(key=lambda session: (session.key, session.start))
        self._open.clear()
        self._emitted += len(emitted)
        return emitted


def sessions_from(
    events: Sequence[Event] | Iterable[Event],
    gap_ms: int,
    allowed_lateness_ms: int = 0,
) -> tuple[Session, ...]:
    """Batch convenience: sessionize a finite list and return it sorted."""
    sessionizer = Sessionizer(gap_ms=gap_ms, allowed_lateness_ms=allowed_lateness_ms)
    emitted = sessionizer.add_all(events)
    emitted.extend(sessionizer.flush())
    emitted.sort(key=lambda session: (session.key, session.start))
    return tuple(emitted)
