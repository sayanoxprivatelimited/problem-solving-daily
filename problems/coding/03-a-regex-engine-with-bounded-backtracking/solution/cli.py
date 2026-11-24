"""CLI: `python -m solution.cli PATTERN --text '...' [--findall|--explain|--disasm]`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.main import (
    DEFAULT_BUDGET,
    BudgetExceeded,
    PatternError,
    compile,
    escape,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regex",
        description="Search with a small, bounded-backtracking regex engine.",
        epilog=(
            "Examples:\n"
            "  python -m solution.cli '(\\\\w+)@(\\\\w+)' --text 'ada@example.com'\n"
            "  python -m solution.cli '\\\\d+' --text 'a1b22' --findall\n"
            "  python -m solution.cli 'a+(b|c)' --explain\n"
            "  python -m solution.cli 'a+(b|c)' --disasm\n"
            "  python -m solution.cli '(a+)+b' --text aaaaaaaaaaaaaaaaaaaa --budget 5000"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pattern", help="the pattern to compile")
    parser.add_argument("--text", default="", help="text to search (default: empty)")
    parser.add_argument("--file", metavar="PATH", help="read the text from a file instead")
    parser.add_argument("--findall", action="store_true", help="print every match, one per line")
    parser.add_argument("--replace", metavar="TEXT", help="replace every match with TEXT")
    parser.add_argument("--explain", action="store_true", help="print the parsed AST")
    parser.add_argument("--disasm", action="store_true", help="print the compiled program")
    parser.add_argument("--ignore-case", action="store_true", help="case-insensitive matching")
    parser.add_argument("--multiline", action="store_true", help="^ and $ match at line breaks")
    parser.add_argument(
        "--budget", type=int, default=DEFAULT_BUDGET, help=f"VM step budget (default: {DEFAULT_BUDGET})"
    )
    parser.add_argument("--escape", action="store_true", help="escape PATTERN instead of compiling it")
    return parser


def _read_text(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    return args.text


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.escape:
        print(escape(args.pattern))
        return 0

    try:
        regex = compile(
            args.pattern, ignore_case=args.ignore_case, multiline=args.multiline
        )
    except PatternError as error:
        print(f"error: {error}", file=sys.stderr)
        print(f"       {args.pattern}", file=sys.stderr)
        print(f"       {' ' * error.position}^", file=sys.stderr)
        return 2

    if args.explain:
        print(regex.explain())
        return 0
    if args.disasm:
        print("\n".join(regex.disassemble()))
        return 0

    text = _read_text(args)
    try:
        if args.replace is not None:
            print(regex.sub(args.replace, text, budget=args.budget))
        elif args.findall:
            for found in regex.findall(text, budget=args.budget):
                print(found)
        else:
            match = regex.search(text, budget=args.budget)
            if match is None:
                print("no match")
                return 1
            print(f"{match.group(0)!r} at {match.start}-{match.end}")
            for index, group in enumerate(match.groups(), start=1):
                print(f"  group {index}: {group!r}")
    except BudgetExceeded as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
