"""Turn a raw nginx access log into a small operations report.

A real log is never clean: probes hammer ``/health``, a client sends something
that is not a request at all, and a killed worker leaves half a line behind.
So parsing, aggregation and rendering are kept separate — each one is testable
on its own, and a single broken line can never take the report down.

Example
-------
>>> line = ('203.0.113.10 - - [20/Nov/2026:09:05:11 +0000] '
...         '"GET /api/users HTTP/1.1" 200 512 "-" "curl/8.0" 0.023')
>>> entry = parse_line(line)
>>> entry.path, entry.status, entry.latency
('/api/users', 200, 0.023)
>>> percentile([0.001, 0.002, 0.004, 0.008, 0.023, 0.031, 0.045, 0.104, 0.912, 1.203], 0.95)
1.203
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

TIMESTAMP_FORMAT = "%d/%b/%Y:%H:%M:%S %z"

# ip ident user [time_local] "METHOD path protocol" status bytes "referer" "ua" request_time
LINE_PATTERN = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[(?P<timestamp>[^\]]+)\] "
    r'"(?P<method>[A-Z]+) (?P<path>\S+)(?: [^"]*)?" '
    r"(?P<status>\d{3}) (?P<size>\d+|-) "
    r'"[^"]*" "[^"]*" (?P<latency>\d+(?:\.\d+)?)$'
)


@dataclass(frozen=True)
class LogEntry:
    """One successfully parsed request."""

    ip: str
    timestamp: datetime
    method: str
    path: str
    status: int
    size: int | None
    latency: float

    @property
    def is_error(self) -> bool:
        return self.status >= 400


@dataclass(frozen=True)
class Report:
    """Everything the on-call engineer asked for, in one immutable object."""

    total_requests: int
    malformed_lines: int
    unique_clients: int
    error_rate: float
    p95_latency_ms: float
    busiest_hour: str | None
    busiest_hour_hits: int
    top_paths: tuple[tuple[str, int], ...]


def parse_line(line: str) -> LogEntry:
    """Parse one access-log line, raising `ValueError` when it does not fit."""
    match = LINE_PATTERN.match(line.strip())
    if match is None:
        raise ValueError(f"unrecognised log line: {line[:120]!r}")

    try:
        timestamp = datetime.strptime(match["timestamp"], TIMESTAMP_FORMAT)
    except ValueError as exc:  # e.g. "32/Nov/2026:00:00:00 +0000"
        raise ValueError(f"bad timestamp {match['timestamp']!r}") from exc

    raw_size = match["size"]
    return LogEntry(
        ip=match["ip"],
        timestamp=timestamp,
        method=match["method"],
        path=match["path"],
        status=int(match["status"]),
        size=None if raw_size == "-" else int(raw_size),
        latency=float(match["latency"]),
    )


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with rank >= ceil(q * n)."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0 < q <= 1:
        raise ValueError(f"q must be in (0, 1], got {q}")
    ordered = sorted(values)
    rank = math.ceil(q * len(ordered))
    return ordered[rank - 1]


def analyze(lines: Iterable[str], top_n: int = 5) -> Report:
    """Aggregate an iterable of log lines into a `Report` (streaming, O(distinct))."""
    if top_n < 0:
        raise ValueError("top_n must be non-negative")

    total = 0
    malformed = 0
    clients: set[str] = set()
    errors = 0
    latencies: list[float] = []
    path_hits: Counter[str] = Counter()
    hour_hits: Counter[str] = Counter()

    for line in lines:
        if not line.strip():
            continue  # blank lines are noise, not corruption
        try:
            entry = parse_line(line)
        except ValueError:
            malformed += 1
            continue

        total += 1
        clients.add(entry.ip)
        errors += entry.is_error
        latencies.append(entry.latency)
        path_hits[entry.path] += 1
        hour_hits[entry.timestamp.strftime("%Y-%m-%d %H:00")] += 1

    top_paths = tuple(
        sorted(path_hits.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    )
    busiest_hour, busiest_hits = None, 0
    if hour_hits:
        busiest_hour, busiest_hits = sorted(
            hour_hits.items(), key=lambda item: (-item[1], item[0])
        )[0]

    return Report(
        total_requests=total,
        malformed_lines=malformed,
        unique_clients=len(clients),
        error_rate=(errors / total) if total else 0.0,
        p95_latency_ms=round(percentile(latencies, 0.95) * 1000, 3) if latencies else 0.0,
        busiest_hour=busiest_hour,
        busiest_hour_hits=busiest_hits,
        top_paths=top_paths,
    )


def analyze_file(path: str | Path, top_n: int = 5) -> Report:
    """Convenience wrapper that streams a file instead of a list of lines."""
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        return analyze(handle, top_n=top_n)


def render(report: Report) -> str:
    """Format a report as the table an on-call engineer wants to skim."""
    hour = report.busiest_hour or "n/a"
    lines = [
        "nginx access log report",
        "=======================",
        f"requests        {report.total_requests}"
        f"  (malformed skipped: {report.malformed_lines})",
        f"unique clients  {report.unique_clients}",
        f"error rate      {report.error_rate * 100:.1f}%",
        f"p95 latency     {report.p95_latency_ms:.1f} ms",
        f"busiest hour    {hour}  ({report.busiest_hour_hits} hits)",
        "",
        "top paths",
    ]
    if report.top_paths:
        width = max(len(path) for path, _ in report.top_paths)
        lines += [f"  {hits:>6}  {path:<{width}}" for path, hits in report.top_paths]
    else:
        lines.append("  (no requests)")
    return "\n".join(lines)
