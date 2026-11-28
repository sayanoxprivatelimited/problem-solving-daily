r"""Aggregate an out-of-order event stream by *event time*, with watermarks.

A stream processor cannot wait for the end of the world before it answers "how many
clicks per 10-second window?". It has to decide, from the data alone, when a window is
*probably* complete — that decision is a **watermark**, and the whole difficulty of the
problem lives in the word "probably".

The rules this module implements:

* **Event time, not arrival time.** A window is a function of the event's timestamps,
  so an event that arrives after a hundred newer ones still lands in its own window.
* **A watermark is a promise.** ``watermark = max timestamp seen so far - lateness``:
  "no event older than this is expected again". It only moves forward.
* **Emission is once, and ordered.** A window is emitted the moment its ``end`` is at or
  below the watermark, never before, never twice — sorted by ``(end, start, key)`` so two
  runs over the same input produce byte-identical output.
* **Late data has two honest answers.** Once a window is emitted, an event inside it is
  either dropped (``mode="drop"``, counted) or corrected in the open: the old aggregate
  is retracted, the new one is emitted, and a downstream reader that applies both gets
  the right answer (``mode="retract"``).

    >>> events = [Event("a", 1), Event("a", 2), Event("a", 11)]
    >>> [r.window_start for r in aggregate(events, size=10)]
    [0.0, 10.0]

    >>> late = [Event("a", 1), Event("a", 25), Event("a", 5, id="late")]
    >>> [(r.window_end, r.count, r.retracted) for r in aggregate(late, size=10, lateness=0)]
    [(10.0, 1, False), (10.0, 1, True), (10.0, 2, False), (30.0, 1, False)]

The first record is the window ``[0, 10)`` as it was believed; the second retracts that
belief; the third is what it became. The window ``[20, 30)`` only closes at ``flush()``,
because no event ever moved the watermark past 30. A consumer summing ``total``
must *subtract* retractions and add the rest — which is exactly what
:func:`.net_totals` does.

    >>> late = [Event("a", 1), Event("a", 25), Event("a", 5, id="late")]
    >>> net_totals(aggregate(late, size=10, lateness=0))[( "a", 0)]
    2.0

Memory is bounded by *open* windows, not by the stream: a window is purged from the
open set the moment it is emitted, so a million events over ten windows cost ten
windows plus the closed aggregates a retraction might still need.

    >>> agg = Aggregator(size=10)
    >>> for ts in range(0, 100, 3):
    ...     _ = agg.add(Event("a", ts))
    >>> agg.windows_open, agg.windows_closed
    (1, 9)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Literal, Sequence

__all__ = [
    "AggregationError",
    "Aggregator",
    "Event",
    "Record",
    "Stats",
    "aggregate",
    "net_totals",
    "AGGREGATES",
    "MODES",
]

#: The aggregates a window can carry. ``mean`` is derived, never accumulated: averaging
#: pre-averaged numbers is the classic way to be quietly wrong about an average.
AGGREGATES: tuple[str, ...] = ("count", "sum", "min", "max", "mean")

#: ``drop`` loses late data loudly (it is counted); ``retract`` corrects it downstream.
MODES: tuple[str, ...] = ("drop", "retract")


class AggregationError(ValueError):
    """Raised for a bad configuration or an event that cannot be placed in time."""


@dataclass(frozen=True, slots=True)
class Event:
    """One observation, positioned in *event time*.

    ``id`` is optional; when it is present and deduplication is on, a redelivered event
    is ignored and counted rather than double-counted. ``value`` defaults to 1, which
    makes ``sum`` a row count for free.
    """

    key: str
    ts: float
    value: float = 1.0
    id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise AggregationError(f"key must be a string, got {type(self.key).__name__}")
        if isinstance(self.ts, bool) or not isinstance(self.ts, (int, float)):
            raise AggregationError(f"ts must be a number, got {type(self.ts).__name__}")
        if not math.isfinite(self.ts):
            raise AggregationError(f"ts must be finite, got {self.ts!r}")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise AggregationError(f"value must be a number, got {type(self.value).__name__}")
        if not math.isfinite(self.value):
            raise AggregationError(f"value must be finite, got {self.value!r}")
        if self.id is not None and not isinstance(self.id, str):
            raise AggregationError(f"id must be a string or None, got {type(self.id).__name__}")


def _plain(value: float) -> float | int:
    """Emit ``10`` rather than ``10.0`` for a boundary that is a whole number."""
    return int(value) if float(value).is_integer() else value


@dataclass(frozen=True, slots=True)
class Record:
    """One emission: either an aggregate, or the retraction of one (``retracted=True``)."""

    key: str
    window_start: float
    window_end: float
    count: int
    total: float
    minimum: float | None
    maximum: float | None
    mean: float | None
    retracted: bool = False

    def as_dict(self) -> dict[str, object]:
        """The JSON shape a `--format jsonl` CLI prints."""
        return {
            "key": self.key,
            "window_start": _plain(self.window_start),
            "window_end": _plain(self.window_end),
            "count": self.count,
            "total": self.total,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean,
            "retracted": self.retracted,
        }


@dataclass(slots=True)
class Stats:
    """Counters that make the loss visible instead of silent."""

    events_in: int = 0
    records_out: int = 0
    duplicates: int = 0
    dropped_late: int = 0
    windows_emitted: int = 0
    retractions: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "events_in": self.events_in,
            "records_out": self.records_out,
            "duplicates": self.duplicates,
            "dropped_late": self.dropped_late,
            "windows_emitted": self.windows_emitted,
            "retractions": self.retractions,
        }


@dataclass(slots=True)
class _Acc:
    """The mutable accumulator behind one (key, window) pair."""

    start: float
    end: float
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def push(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None or value < self.minimum else self.minimum
        self.maximum = value if self.maximum is None or value > self.maximum else self.maximum

    def record(self, key: str, *, retracted: bool = False) -> Record:
        mean = None if self.count == 0 else self.total / self.count
        return Record(
            key=key,
            window_start=self.start,
            window_end=self.end,
            count=self.count,
            total=self.total,
            minimum=self.minimum,
            maximum=self.maximum,
            mean=mean,
            retracted=retracted,
        )

    def copy(self) -> "_Acc":
        return _Acc(self.start, self.end, self.count, self.total, self.minimum, self.maximum)


class Aggregator:
    """A watermark-driven windowed aggregator over one or more keys.

    ``size`` and ``hop`` define the windows: ``hop == size`` is a tumbling window,
    ``hop < size`` a sliding one, and an event belongs to every window that contains its
    timestamp. ``lateness`` is how long the aggregator is willing to wait for stragglers
    before it emits a window — the size of the promise it makes to the future.
    """

    def __init__(
        self,
        size: float,
        hop: float | None = None,
        lateness: float = 0,
        mode: Literal["drop", "retract"] = "retract",
        *,
        aggregates: Sequence[str] = AGGREGATES,
        dedupe: bool = True,
    ) -> None:
        if not isinstance(size, (int, float)) or isinstance(size, bool) or size <= 0:
            raise AggregationError(f"size must be a positive number, got {size!r}")
        if hop is None:
            hop = size
        if not isinstance(hop, (int, float)) or isinstance(hop, bool) or hop <= 0:
            raise AggregationError(f"hop must be a positive number, got {hop!r}")
        if hop > size:
            # Holes between windows would silently drop data: 10-wide windows every 20
            # seconds leave the second half of every period unaggregated.
            raise AggregationError(f"hop ({hop}) cannot exceed size ({size}): that leaves gaps")
        if not isinstance(lateness, (int, float)) or isinstance(lateness, bool) or lateness < 0:
            raise AggregationError(f"lateness must be a non-negative number, got {lateness!r}")
        if mode not in MODES:
            raise AggregationError(f"mode must be one of {MODES}, got {mode!r}")
        unknown = [name for name in aggregates if name not in AGGREGATES]
        if unknown:
            raise AggregationError(f"unknown aggregate(s) {unknown}; pick from {AGGREGATES}")

        self.size = float(size)
        self.hop = float(hop)
        self.lateness = float(lateness)
        self.mode = mode
        self.aggregates = tuple(aggregates)
        self.dedupe = dedupe

        self._max_ts: float | None = None
        self._open: dict[tuple[str, float], _Acc] = {}
        self._closed: dict[tuple[str, float], _Acc] = {}
        self._seen: set[str] = set()
        self.stats = Stats()

    # ------------------------------------------------------------------ properties

    @property
    def watermark(self) -> float | None:
        """The oldest timestamp still expected, or ``None`` before the first event."""
        return None if self._max_ts is None else self._max_ts - self.lateness

    @property
    def windows_open(self) -> int:
        return len(self._open)

    @property
    def windows_closed(self) -> int:
        return len(self._closed)

    # ------------------------------------------------------------------ ingestion

    def add(self, event: Event) -> list[Record]:
        """Feed one event; return every record that became final because of it."""
        if not isinstance(event, Event):
            raise AggregationError(f"add() takes an Event, got {type(event).__name__}")

        if self.dedupe and event.id is not None:
            if event.id in self._seen:
                self.stats.duplicates += 1
                return []
            self._seen.add(event.id)

        self.stats.events_in += 1
        # The watermark only moves forward. An old event must not un-close a window that
        # was already emitted, or the same window gets emitted twice.
        if self._max_ts is None or event.ts > self._max_ts:
            self._max_ts = event.ts

        records: list[Record] = []
        for start in self._window_starts(event.ts):
            slot = (event.key, start)
            closed = self._closed.get(slot)
            if closed is not None:
                records.extend(self._correct(slot, event, closed))
                continue
            acc = self._open.get(slot)
            if acc is None:
                acc = _Acc(start, start + self.size)
                self._open[slot] = acc
            acc.push(event.value)

        records.extend(self._drain())
        self.stats.records_out += len(records)
        return records

    def extend(self, events: Iterable[Event]) -> list[Record]:
        """Feed many events at once, still emitting in order."""
        out: list[Record] = []
        for event in events:
            out.extend(self.add(event))
        return out

    def flush(self) -> list[Record]:
        """Close the stream: emit every window still open, in the usual order."""
        records = self._emit_all(self._open)
        self._open.clear()
        self.stats.records_out += len(records)
        return records

    # ------------------------------------------------------------------ internals

    def _window_starts(self, ts: float) -> list[float]:
        """Every window start (multiple of ``hop``) whose window contains ``ts``."""
        last = math.floor(ts / self.hop) * self.hop
        spans = max(1, int(math.ceil(self.size / self.hop)))
        starts = []
        for index in range(spans):
            start = last - index * self.hop
            if start + self.size > ts:  # start <= ts < start + size
                starts.append(start)
        return starts

    def _correct(self, slot: tuple[str, float], event: Event, closed: _Acc) -> list[Record]:
        """A straggler arrived for a window that was already emitted."""
        if self.mode == "drop":
            self.stats.dropped_late += 1
            return []

        key = slot[0]
        retraction = closed.record(key, retracted=True)
        updated = closed.copy()
        updated.push(event.value)
        self._closed[slot] = updated
        self.stats.retractions += 1
        return [retraction, updated.record(key)]

    def _drain(self) -> list[Record]:
        """Emit every open window whose end the watermark has reached."""
        watermark = self.watermark
        if watermark is None:
            return []
        ready = [slot for slot in self._open if self._open[slot].end <= watermark]
        if not ready:
            return []
        records: list[Record] = []
        for slot in sorted(ready, key=self._sort_key):
            acc = self._open.pop(slot)
            self._closed[slot] = acc
            records.append(acc.record(slot[0]))
            self.stats.windows_emitted += 1
        return records

    def _emit_all(self, windows: dict[tuple[str, float], _Acc]) -> list[Record]:
        records = []
        for slot in sorted(windows, key=self._sort_key):
            acc = windows[slot]
            self._closed[slot] = acc
            records.append(acc.record(slot[0]))
            self.stats.windows_emitted += 1
        return records

    def _sort_key(self, slot: tuple[str, float]) -> tuple[float, float, str]:
        """Emission order: window end, then start, then key — stable across runs."""
        key, start = slot
        return (start + self.size, start, key)


def aggregate(events: Iterable[Event], **kwargs: object) -> list[Record]:
    """Feed ``events`` through a fresh aggregator, flush, and return every record.

    The convenience wrapper the tests and the CLI both use; ``kwargs`` go to
    :class:`Aggregator`.

    >>> aggregate([Event("a", 0), Event("a", 1)], size=10)[0].count
    2

    >>> aggregate([], size=10)
    []
    """
    agg = Aggregator(**kwargs)  # type: ignore[arg-type]
    records = agg.extend(events)
    records.extend(agg.flush())
    return records


def net_totals(records: Iterable[Record]) -> dict[tuple[str, float], float]:
    """Apply retractions, i.e. what a downstream consumer of the stream would hold.

    >>> recs = aggregate([Event("a", 1), Event("a", 25), Event("a", 4, id="x")], size=10, lateness=0)
    >>> recs[-1].retracted
    False
    >>> net_totals(recs)[("a", 0)]
    2.0
    """
    totals: dict[tuple[str, float], float] = {}
    for record in records:
        slot = (record.key, record.window_start)
        delta = -record.total if record.retracted else record.total
        totals[slot] = totals.get(slot, 0.0) + delta
    return totals


def iter_events(rows: Iterable[dict[str, object]]) -> Iterator[Event]:
    """Build events from decoded JSON objects, with a message that names the bad row."""
    for index, row in enumerate(rows):
        try:
            yield Event(
                key=row["key"],  # type: ignore[arg-type]
                ts=row["ts"],  # type: ignore[arg-type]
                value=row.get("value", 1.0),  # type: ignore[arg-type]
                id=row.get("id"),  # type: ignore[arg-type]
            )
        except KeyError as missing:
            raise AggregationError(f"row {index}: missing field {missing.args[0]!r}") from None
        except AggregationError as exc:
            raise AggregationError(f"row {index}: {exc}") from None
