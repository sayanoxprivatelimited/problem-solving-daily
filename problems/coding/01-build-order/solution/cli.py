"""CLI: turn a manifest into a build plan.

    python -m solution.cli tests/fixtures/packages.json
    python -m solution.cli tests/fixtures/packages.json --changed common
    python -m solution.cli tests/fixtures/packages.json --installed common logging --changed db
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.main import DependencyError, build_order, parse_manifest, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build-order", description="Topological build order for a package manifest."
    )
    parser.add_argument("manifest", help="JSON manifest of packages")
    parser.add_argument("--changed", nargs="*", default=None, help="packages that changed")
    parser.add_argument("--installed", nargs="*", default=[], help="packages already built")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        text = Path(args.manifest).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.manifest}: {exc}", file=sys.stderr)
        return 1

    try:
        packages = parse_manifest(text)
        order = build_order(packages, installed=args.installed, changed=args.changed)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except DependencyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(render(packages, order))
    if args.changed is not None:
        print(f"\n(changed: {', '.join(args.changed) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
