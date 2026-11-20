"""CLI: sessionize a JSONL event stream.

Each input line is `{"ts": <epoch-ms>, "key": "...", "value": "..."}`, `-` reads stdin.

    python -m solution.cli --gap 5000 events.jsonl
    python -m solution.cli --gap 5000 --lateness 2000 --json events.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable, TextIO

from solution.main import Event, Session, Sessionizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sessionize", description="Group out-of-order events into sessions."
    )
    parser.add_argument("path", nargs="?", default="-", help="JSONL file, or - for stdin")
    parser.add_argument("--gap", type=int, required=True, help="session gap in milliseconds")
    parser.add_argument(
        "--lateness", type=int, default=0, help="allowed lateness in milliseconds"
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object per line")
    return parser


def read_events(stream: TextIO) -> list[Event]:
    events: list[Event] = []
    for number, line in enumerate(stream, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            events.append(
                Event(ts=int(payload["ts"]), key=str(payload["key"]), value=str(payload.get("value", "")))
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"line {number}: {error}") from error
    return events


def render(sessions: Iterable[Session]) -> str:
    rows = ["key      start        end          gap(ms)  events"]
    for session in sessions:
        rows.append(
            f"{session.key:<8} {session.start:<12} {session.end:<12} "
            f"{session.duration:<8} {session.count}"
        )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gap < 0 or args.lateness < 0:
        print("error: --gap and --lateness must be >= 0", file=sys.stderr)
        return 2

    try:
        if args.path == "-":
            events = read_events(sys.stdin)
        else:
            with open(args.path, encoding="utf-8") as handle:
                events = read_events(handle)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    sessionizer = Sessionizer(gap_ms=args.gap, allowed_lateness_ms=args.lateness)
    sessions = sessionizer.add_all(events) + sessionizer.flush()

    if args.json:
        for session in sessions:
            print(
                json.dumps(
                    {
                        "key": session.key,
                        "start": session.start,
                        "end": session.end,
                        "duration": session.duration,
                        "events": session.count,
                    }
                )
            )
    else:
        print(render(sessions))

    stats = sessionizer.stats
    print(
        f"\n{len(sessions)} session(s), {stats['events']} event(s) accepted, "
        f"{stats['late_dropped']} too late, watermark {stats['watermark']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
