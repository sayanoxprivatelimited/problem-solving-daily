"""CLI: `python -m solution.cli <folder> [--dry-run] [--undo] [--include-hidden]`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.main import DEFAULT_MANIFEST_NAME, organize, render, undo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="file-organizer",
        description="Sort a messy folder into type folders, with dry-run and undo.",
    )
    parser.add_argument("folder", nargs="?", default=".", help="folder to organise (default: .)")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--undo", action="store_true", help="reverse the last organise run")
    parser.add_argument(
        "--manifest", metavar="PATH", help=f"manifest location (default: <folder>/{DEFAULT_MANIFEST_NAME})"
    )
    parser.add_argument("--include-hidden", action="store_true", help="also move dotfiles")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    folder = Path(args.folder).expanduser()
    manifest = Path(args.manifest).expanduser() if args.manifest else folder / DEFAULT_MANIFEST_NAME

    if args.undo:
        if not manifest.exists():
            print(f"error: no manifest at {manifest}", file=sys.stderr)
            return 1
        try:
            restored = undo(manifest)
        except (OSError, ValueError) as exc:
            print(f"error: cannot undo: {exc}", file=sys.stderr)
            return 1
        print(f"restored {restored} file(s)")
        return 0

    try:
        moves = organize(
            folder,
            dry_run=args.dry_run,
            manifest_path=manifest,
            include_hidden=args.include_hidden,
        )
    except (NotADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(render(moves, dry_run=args.dry_run))
    if moves and not args.dry_run:
        print(f"manifest: {manifest}  (revert with --undo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
