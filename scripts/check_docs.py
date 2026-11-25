#!/usr/bin/env python3
"""Check (or fix) the counts written in README.md against the catalogue.

The page's counts are guarded twice already: `site/data/problems.json` is
generated, and `build_seo.py --check` refuses to let `site/index.html` drift from
it. `README.md` is the one place a human types the numbers by hand, and it has
now drifted three times — 19 problems in the hero, "Twelve" in the lede, and
"632 tests" long after the suite had 700.

So this script owns the one line that carries them:

    **20 problems · 700 tests · 228 doctests · 6 categories · zero dependencies**

Usage
-----
    python3 scripts/check_docs.py           # check, exit 1 on drift
    python3 scripts/check_docs.py --fix     # rewrite the line from the catalogue
    python3 scripts/check_docs.py --quiet   # say nothing when it matches
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DATA = ROOT / "site" / "data" / "problems.json"

#: The one line that carries the totals. Kept tight on purpose: a loose pattern
#: would "fix" counts in prose that were never meant to be maintained.
COUNTS_RE = re.compile(
    r"\*\*(?P<problems>\d+) problems · (?P<tests>\d+) tests · (?P<doctests>\d+) doctests"
    r" · (?P<categories>\d+) categories · (?P<dependencies>[a-z ]+)\*\*"
)


def stats() -> dict[str, int]:
    """Totals from the generated catalogue, which is the only source of truth."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    return {
        "problems": data["stats"]["problems"],
        "tests": data["stats"]["tests"],
        "doctests": data["stats"]["doctests"],
        "categories": len(data["categories"]),
    }


def current(text: str) -> tuple[re.Match[str], dict[str, str]] | tuple[None, None]:
    """The counts line and its groups, or ``(None, None)`` when it is missing."""
    match = COUNTS_RE.search(text)
    return (match, match.groupdict()) if match else (None, None)


def render(expected: dict[str, int], dependencies: str = "zero dependencies") -> str:
    return (
        f"**{expected['problems']} problems · {expected['tests']} tests"
        f" · {expected['doctests']} doctests · {expected['categories']} categories"
        f" · {dependencies}**"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true", help="rewrite README.md instead of failing")
    parser.add_argument("--quiet", action="store_true", help="print nothing when the counts match")
    args = parser.parse_args(argv)

    if not DATA.is_file():
        print("error: site/data/problems.json is missing — run: python3 scripts/build_catalog.py", file=sys.stderr)
        return 2

    text = README.read_text(encoding="utf-8")
    match, found = current(text)
    if match is None or found is None:
        print("error: README.md has no \"**N problems · N tests · …**\" line to check", file=sys.stderr)
        return 2

    expected = stats()
    stale = {key: (found[key], str(value)) for key, value in expected.items() if found[key] != str(value)}

    if not stale:
        if not args.quiet:
            print(f"README counts match the catalogue: {render(expected)}")
        return 0

    summary = ", ".join(f"{key} {before} → {after}" for key, (before, after) in stale.items())
    if args.fix:
        dependencies = found["dependencies"].strip() or "zero dependencies"
        README.write_text(text.replace(match.group(0), render(expected, dependencies)), encoding="utf-8")
        print(f"README counts updated: {summary}")
        return 0

    print(f"README counts are stale: {summary}", file=sys.stderr)
    print(f"  expected: {render(expected)}", file=sys.stderr)
    print(f"  found:    {match.group(0)}", file=sys.stderr)
    print("  fix with: python3 scripts/check_docs.py --fix", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
