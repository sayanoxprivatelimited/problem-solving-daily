"""CLI: dedupe a JSONL stream with a bounded memory budget.

    python -m solution.cli events.jsonl --key id
    python -m solution.cli events.jsonl --key id --max-keys 2 --stats
    cat events.jsonl | python -m solution.cli - --key user.id --in-memory --stats

Exit codes: 0 success, 2 unreadable input or a record without a key.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, Iterator

from solution.main import DedupeError, Deduper, dedupe_in_memory, iter_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dedupe", description="Bounded-memory JSONL dedupe.")
    parser.add_argument("path", help="JSONL file, or - for stdin")
    parser.add_argument("--key", default="id", help="key field, dotted path, or 'json:<expr>'")
    parser.add_argument("--shards", type=int, default=8, help="hash buckets per pass")
    parser.add_argument("--max-keys", type=int, default=10_000, help="distinct keys per bucket")
    parser.add_argument("--max-depth", type=int, default=3, help="how many times a bucket may split")
    parser.add_argument("--tmp-dir", default=None, help="where to spool (default: system temp)")
    parser.add_argument("--in-memory", action="store_true", help="use the naive set (unbounded)")
    parser.add_argument("--stats", action="store_true", help="print stats to stderr")
    parser.add_argument("--json", action="store_true", help="print stats as JSON")
    parser.add_argument("-o", "--output", help="write JSONL here instead of stdout")
    return parser


def resolve_key(spec: str) -> Any:
    """Turn ``user.id`` into a callable that walks nested objects."""
    path = spec.split(".")

    def getter(record: dict[str, Any]) -> Any:
        value: Any = record
        for part in path:
            if not isinstance(value, dict) or part not in value:
                raise KeyError(spec)
            value = value[part]
        return value

    return getter


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.shards < 2 or args.max_keys < 1:
        print("error: --shards must be >= 2 and --max-keys >= 1", file=sys.stderr)
        return 2

    key = resolve_key(args.key)

    def source() -> Iterator[dict[str, Any]]:
        if args.path == "-":
            yield from iter_jsonl(sys.stdin, source="<stdin>")
        else:
            with open(args.path, encoding="utf-8") as handle:
                yield from iter_jsonl(handle, source=args.path)

    deduper = Deduper(
        key=key,
        shards=args.shards,
        max_keys=args.max_keys,
        max_depth=args.max_depth,
        tmp_dir=args.tmp_dir,
    )
    records: Iterable[dict[str, Any]]
    records = dedupe_in_memory(source(), key=key) if args.in_memory else deduper.dedupe(source())

    written = 0
    try:
        stream = sys.stdout if args.output is None else open(args.output, "w", encoding="utf-8")
        try:
            for record in records:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
                written += 1
        finally:
            if args.output is not None:
                stream.close()
    except DedupeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return 2

    if args.stats or args.json:
        stats = dict(deduper.stats)
        stats["written"] = written
        stats["mode"] = "memory" if args.in_memory else "sharded"
        if args.in_memory:
            stats.setdefault("peak_keys", written)
        if args.json:
            print(json.dumps(stats, sort_keys=True), file=sys.stderr)
        else:
            print(
                f"{stats['records']} record(s) in · {stats['written']} out · "
                f"{stats['records'] - stats['written']} duplicate(s) · "
                f"peak {stats.get('peak_keys', 0)} key(s) per bucket · "
                f"{stats.get('buckets', 0)} bucket(s) · {stats.get('reshards', 0)} re-shard(s) · "
                f"{stats.get('passes', 0)} pass(es) · {stats['mode']}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
