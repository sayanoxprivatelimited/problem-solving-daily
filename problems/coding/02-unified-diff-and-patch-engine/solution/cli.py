"""CLI: produce or apply a unified diff.

    python -m solution.cli diff old.txt new.txt
    python -m solution.cli diff old.txt new.txt --context 1 --stat
    python -m solution.cli apply change.patch old.txt -o new.txt

Exit codes: 0 success, 1 the patch did not apply, 2 the input was unusable.
"""
from __future__ import annotations

import argparse
import sys

from solution.main import PatchError, apply_patch, diff, summarize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patchwork", description="Diff and patch text files.")
    sub = parser.add_subparsers(dest="command", required=True)

    maker = sub.add_parser("diff", help="print the unified diff between two files")
    maker.add_argument("old", help="the original file")
    maker.add_argument("new", help="the changed file")
    maker.add_argument("--context", type=int, default=3, help="unchanged lines around a change")
    maker.add_argument("--from-name", default=None, help="name to use in the --- header")
    maker.add_argument("--to-name", default=None, help="name to use in the +++ header")
    maker.add_argument("--stat", action="store_true", help="print a one-line summary to stderr")
    maker.add_argument("-o", "--output", help="write the patch here instead of stdout")

    applier = sub.add_parser("apply", help="apply a patch to a file")
    applier.add_argument("patch", help="the patch file (use - for stdin)")
    applier.add_argument("target", help="the file to patch")
    applier.add_argument("-o", "--output", help="write the result here instead of stdout")
    return parser


def read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: str | None, text: str) -> None:
    if path is None:
        sys.stdout.write(text)
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "diff":
            old = read_text(args.old)
            new = read_text(args.new)
            patch = diff(
                old,
                new,
                context=args.context,
                from_name=args.from_name or args.old,
                to_name=args.to_name or args.new,
            )
            if patch:
                write_text(args.output, patch)
            if args.stat:
                summary = summarize(patch) if patch else "0 hunk(s), +0 -0"
                print(f"{summary} ({args.old} -> {args.new})", file=sys.stderr)
            return 0

        target = read_text(args.target)
        patch_text = read_text(args.patch)
        result = apply_patch(target, patch_text)
        write_text(args.output, result)
        return 0
    except PatchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
