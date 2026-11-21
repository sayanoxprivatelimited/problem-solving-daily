r"""Dedupe a JSONL stream whose keys do not fit in memory.

The naive dedupe keeps every key it has seen in a ``set``. At 40 million records that
set is several gigabytes, and the job does not fail cleanly — the OOM killer takes it
out halfway through and leaves a truncated file.

This module bounds memory instead of hoping. The stream is read **once**, and every
record is spooled to one of ``shards`` temporary files, chosen by a hash of its key.
Duplicate keys always land in the same shard, so each shard can then be deduped on its
own with a set that is a fraction of the size. Survivors are merged back by sequence
number, so the output keeps the input's order.

A shard that *still* does not fit is re-sharded with a different salt, recursively —
hot keys are real, and "one bucket holds everything" is the failure mode that makes
naive external algorithms fall over.

    >>> events = [{"id": "a"}, {"id": "b"}, {"id": "a"}, {"id": "c"}, {"id": "b"}]
    >>> [event["id"] for event in dedupe_in_memory(events, key="id")]
    ['a', 'b', 'c']

    >>> [event["id"] for event in dedupe(events, key="id", shards=2, max_keys=2)]
    ['a', 'b', 'c']

The sharded path is bounded: with four shards and a budget of one key per bucket, no
bucket ever holds more than one distinct key:

    >>> events = [{"id": f"k{index}"} for index in range(20)]
    >>> deduper = Deduper(key="id", shards=4, max_keys=2)
    >>> survivors = list(deduper.dedupe(events))
    >>> len(survivors), deduper.stats["peak_keys"] <= 3
    (20, True)

Order is preserved even when the duplicates are far apart:

    >>> events = [{"id": "x", "n": 1}, {"id": "y", "n": 2}, {"id": "x", "n": 3}]
    >>> [event["n"] for event in dedupe(events, key="id", shards=3, max_keys=5)]
    [1, 2]

A record without a key is refused by line number, not silently dropped:

    >>> try:
    ...     list(dedupe([{"id": "a"}, {"nope": 1}], key="id"))
    ... except DedupeError as error:
    ...     print(error)
    line 2: record has no 'id' field
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

__all__ = [
    "DedupeError",
    "Deduper",
    "MemoryBudgetExceeded",
    "dedupe",
    "dedupe_in_memory",
    "iter_jsonl",
    "read_jsonl",
    "write_jsonl",
]

KeySpec = str | Callable[[dict[str, Any]], Any]


class DedupeError(ValueError):
    """A record has no usable key, or a spool file cannot be written."""


class MemoryBudgetExceeded(DedupeError):
    """Re-sharding hit its depth limit and a bucket is still too large."""


def _key_function(key: KeySpec) -> Callable[[dict[str, Any]], Any]:
    if callable(key):
        return key
    field_name = key

    def getter(record: dict[str, Any]) -> Any:
        if field_name not in record:
            raise KeyError(field_name)
        return record[field_name]

    return getter


def _digest(value: Any, depth: int) -> int:
    """A 64-bit, order-insensitive digest of a JSON-serialisable key."""
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as error:
        raise DedupeError(f"key is not JSON-serialisable: {error}") from error
    raw = hashlib.blake2b(
        canonical.encode("utf-8"), digest_size=8, salt=f"dedupe{depth}".encode()
    ).digest()
    return int.from_bytes(raw, "big")


# -------------------------------------------------------------------- io -- #
def iter_jsonl(lines: Iterable[str], *, source: str = "<stream>") -> Iterator[dict[str, Any]]:
    """Yield parsed objects from JSONL text, skipping blank lines."""
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise DedupeError(f"{source}: line {number}: invalid JSON ({error.msg})") from error
        if not isinstance(record, dict):
            raise DedupeError(f"{source}: line {number}: expected a JSON object")
        yield record


def read_jsonl(path: str) -> Iterator[dict[str, Any]]:
    """Stream a JSONL file without loading it."""
    with open(path, encoding="utf-8") as handle:
        yield from iter_jsonl(handle, source=path)


def write_jsonl(records: Iterable[dict[str, Any]], path: str) -> int:
    """Write records as JSONL; returns how many were written."""
    written = 0
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            written += 1
    return written


# --------------------------------------------------------------- baseline -- #
def dedupe_in_memory(records: Iterable[dict[str, Any]], key: KeySpec = "id") -> Iterator[dict[str, Any]]:
    """The obvious version, kept as the correctness baseline (and the counter-example)."""
    getter = _key_function(key)
    seen: set[str] = set()
    for number, record in enumerate(records, start=1):
        try:
            digest = _digest(getter(record), 0)
        except KeyError as error:
            raise DedupeError(f"line {number}: record has no {error.args[0]!r} field") from error
        if digest in seen:
            continue
        seen.add(digest)
        yield record


# ----------------------------------------------------------------- deduper -- #
@dataclass
class Deduper:
    """Bounded-memory dedupe: shard by hash, dedupe each shard, merge by sequence."""

    key: KeySpec = "id"
    shards: int = 8
    max_keys: int = 10_000
    max_depth: int = 3
    tmp_dir: str | None = None
    stats: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.shards < 2:
            raise DedupeError("shards must be >= 2")
        if self.max_keys < 1:
            raise DedupeError("max_keys must be >= 1")
        self._getter = _key_function(self.key)
        self.stats = {
            "records": 0,
            "duplicates": 0,
            "buckets": 0,
            "reshards": 0,
            "peak_keys": 0,
            "passes": 0,
        }

    # -- public ---------------------------------------------------------- #
    def dedupe(self, records: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """Yield the first occurrence of every key, in input order."""
        for name in self.stats:
            self.stats[name] = 0

        workdir = tempfile.mkdtemp(prefix="dedupe-", dir=self.tmp_dir)
        try:
            spool = os.path.join(workdir, "spool")
            self._spool(((index, record) for index, record in enumerate(records)), spool, depth=0)
            leaves: list[str] = []
            for bucket in sorted(os.listdir(spool)):
                leaves.extend(self._dedupe_bucket(os.path.join(spool, bucket), depth=0))

            streams = [self._stream(path) for path in leaves]
            self.stats["passes"] += 1
            for _, record in heapq.merge(*streams, key=lambda entry: entry[0]):
                yield record
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # -- internals ------------------------------------------------------- #
    def _spool(
        self, entries: Iterable[tuple[int, dict[str, Any]]], directory: str, depth: int
    ) -> None:
        """Write ``(sequence, record)`` pairs to their hash buckets, streaming."""
        os.makedirs(directory, exist_ok=True)
        handles = {
            index: open(os.path.join(directory, f"{index:04d}.jsonl"), "w", encoding="utf-8")
            for index in range(self.shards)
        }
        try:
            for number, (sequence, record) in enumerate(entries, start=1):
                try:
                    digest = _digest(self._getter(record), depth)
                except KeyError as error:
                    raise DedupeError(
                        f"line {number}: record has no {error.args[0]!r} field"
                    ) from error
                handle = handles[digest % self.shards]
                handle.write(json.dumps([sequence, record], sort_keys=True) + "\n")
                self.stats["records"] += 1
            self.stats["passes"] += 1
        finally:
            for handle in handles.values():
                handle.close()

    def _stream(self, path: str) -> Iterator[tuple[int, dict[str, Any]]]:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    sequence, record = json.loads(line)
                    yield int(sequence), record

    def _dedupe_bucket(self, path: str, depth: int) -> list[str]:
        """Dedupe one bucket in memory, or split it again when it does not fit."""
        seen: set[int] = set()
        survivors: list[tuple[int, dict[str, Any]]] = []
        duplicates = 0
        self.stats["buckets"] += 1

        for sequence, record in self._stream(path):
            digest = _digest(self._getter(record), depth)
            if digest in seen:
                duplicates += 1
                continue
            seen.add(digest)
            survivors.append((sequence, record))
            self.stats["peak_keys"] = max(self.stats["peak_keys"], len(seen))
            if len(seen) > self.max_keys:
                # Too big: give the memory back and split this bucket instead.
                break

        if len(seen) <= self.max_keys:
            self.stats["duplicates"] += duplicates
            with open(path, "w", encoding="utf-8") as handle:
                for sequence, record in survivors:
                    handle.write(json.dumps([sequence, record], sort_keys=True) + "\n")
            return [path]

        if depth >= self.max_depth:
            raise MemoryBudgetExceeded(
                f"a bucket still holds more than {self.max_keys} keys after {depth + 1} splits; "
                "raise max_keys, shards, or max_depth"
            )

        # Re-shard with a new salt: duplicates still land together, distinct keys spread.
        self.stats["reshards"] += 1
        nested = path + f".split{depth + 1}"
        self._spool(self._stream(path), nested, depth=depth + 1)
        os.remove(path)
        leaves: list[str] = []
        for bucket in sorted(os.listdir(nested)):
            leaves.extend(self._dedupe_bucket(os.path.join(nested, bucket), depth=depth + 1))
        return leaves


def dedupe(
    records: Iterable[dict[str, Any]],
    key: KeySpec = "id",
    shards: int = 8,
    max_keys: int = 10_000,
    max_depth: int = 3,
    tmp_dir: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Module-level convenience wrapper around :class:`Deduper`."""
    return Deduper(
        key=key, shards=shards, max_keys=max_keys, max_depth=max_depth, tmp_dir=tmp_dir
    ).dedupe(records)
