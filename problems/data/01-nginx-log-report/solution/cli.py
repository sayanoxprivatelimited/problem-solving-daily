"""CLI entry point: `python -m solution.cli <access.log> [--top N] [--json]`."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from solution.main import analyze_file, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nginx-log-report",
        description="Summarise an nginx access log (combined format + $request_time).",
    )
    parser.add_argument("logfile", help="path to access.log, or - for stdin")
    parser.add_argument("--top", type=int, default=5, metavar="N", help="how many paths to show")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.top < 0:
        print("error: --top must be >= 0", file=sys.stderr)
        return 2

    if args.logfile == "-":
        from solution.main import analyze

        report = analyze(sys.stdin, top_n=args.top)
    else:
        try:
            report = analyze_file(args.logfile, top_n=args.top)
        except OSError as exc:
            print(f"error: cannot read {args.logfile}: {exc}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
