"""CLI: aggregate a JSONL event stream by event time, with watermarks.

    python -m solution.cli events.jsonl --size 10
    cat events.jsonl | python -m solution.cli - --size 60 --hop 15 --lateness 5
    python -m solution.cli events.jsonl --size 10 --mode drop --stats --format tsv

Each input line is ``{"key": "…", "ts": 1730000000, "value": 1.0, "id": "…"}``; ``value``
defaults to 1 and ``id`` is optional. Output is one JSON object per emitted record, in
the aggregator's own order, so a diff between two runs is meaningful.

Exit codes: 0 success, 2 unreadable input or a malformed record.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Iterator, TextIO

from solution.main import AggregationError, Aggregator, Event, Record, iter_events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="window", description="Watermark-driven windowed aggregation.")
    parser.add_argument("path", help="JSONL file, or - for stdin")
    parser.add_argument("--size", type=float, required=True, help="window size in timestamp units")
    parser.add_argument("--hop", type=float, default=None, help="window hop (default: size, i.e. tumbling)")
    parser.add_argument("--lateness", type=float, default=0.0, help="how far behind the watermark a late event may be")
    parser.add_argument("--mode", choices=("drop", "retract"), default="retract", help="what to do with very late data")
    parser.add_argument("--no-dedupe", action="store_true", help="count redelivered ids twice")
    parser.add_argument("--format", choices=("jsonl", "tsv"), default="jsonl", help="output shape")
    parser.add_argument("--stats", action="store_true", help="print counters to stderr")
    return parser


def open_input(path: str) -> TextIO:
    if path == "-":
        return sys.stdin
    try:
        return open(path, encoding="utf-8")
    except OSError as exc:
        raise AggregationError(f"cannot read {path}: {exc.strerror}") from None


def decode(lines: Iterator[str]) -> Iterator[dict[str, object]]:
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AggregationError(f"line {number}: not JSON ({exc.msg})") from None
        if not isinstance(row, dict):
            raise AggregationError(f"line {number}: expected an object, got {type(row).__name__}")
        yield row


def format_record(record: Record, fmt: str) -> str:
    if fmt == "tsv":
        fields = [
            record.key,
            _num(record.window_start),
            _num(record.window_end),
            str(record.count),
            _num(record.total),
            "retract" if record.retracted else "emit",
        ]
        return "\t".join(fields)
    return json.dumps(record.as_dict(), sort_keys=True)


def _num(value: float | None) -> str:
    """Render whole numbers without a trailing ``.0`` — timestamps are usually ints."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        aggregator = Aggregator(
            size=args.size,
            hop=args.hop,
            lateness=args.lateness,
            mode=args.mode,
            dedupe=not args.no_dedupe,
        )
    except AggregationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    source = sys.stdin if args.path == "-" else None
    try:
        source = source or open_input(args.path)
        # Stream: one decoding pass, one event at a time, no list of the whole input.
        for event in iter_events(decode(iter(source.readline, ""))):
            for record in aggregator.add(event):
                print(format_record(record, args.format))
        for record in aggregator.flush():
            print(format_record(record, args.format))
    except AggregationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot read {args.path}: {exc.strerror}", file=sys.stderr)
        return 2
    finally:
        if source is not sys.stdin and source is not None:
            source.close()

    if args.stats:
        print(json.dumps(aggregator.stats.as_dict(), sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
