#!/usr/bin/env python3
"""Scaffold a new problem for today's (or any) day folder.

Examples
--------
    python scripts/new_problem.py "Trapping Rain Water" --topic "Two Pointers"
    python scripts/new_problem.py "Word Ladder" --difficulty Hard --date 2026-11-21
    python scripts/new_problem.py "Two Sum" --dry-run

It creates
  * days/<date>/<slug>.py          — solution stub from templates/solution_template.py
  * days/<date>/test_<slug>.py     — unittest stub
  * days/<date>/README.md          — day README (only if the folder is new)
  * a new row in progress.md       — before the `progress-table-end` marker

Nothing is overwritten: existing files are skipped and reported.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
DAYS_DIR = ROOT / "days"
PROGRESS = ROOT / "progress.md"
PROGRESS_MARKER = "<!-- progress-table-end -->"

SOLUTION_STUB = '''"""LeetCode <number> — {title} ({difficulty}).

Problem
-------
<What does the input mean, what must be returned?>

Examples
--------
>>> solution([1, 2, 3])  # TODO: real example
...

Approach — <pattern>
--------------------
<Why it works, in your own words. State the invariant.>

Complexity
----------
Time  : O(?)
Space : O(?)

Edge cases
----------
* <empty input / single element / duplicates ...>
"""
from __future__ import annotations


def solution(nums: list[int]) -> int:
    """TODO: replace this signature with the real one."""
    raise NotImplementedError
'''

TEST_STUB = '''"""Tests for {title}."""
from __future__ import annotations

import unittest

from {slug} import solution


class Test{title_class}(unittest.TestCase):
    def test_examples(self) -> None:
        self.fail("write the first test")


if __name__ == "__main__":
    unittest.main()
'''


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    if not slug:
        raise ValueError(f"cannot build a file name from {title!r}")
    return slug


def camel_case(title: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", title)
    name = "".join(p.capitalize() for p in parts)
    return name if name else "Problem"


def next_row_number(progress_text: str) -> int:
    rows = re.findall(r"^\|\s*(\d+)\s*\|", progress_text, flags=re.MULTILINE)
    return max((int(r) for r in rows), default=0) + 1


def progress_row(number: int, date: str, title: str, slug: str, difficulty: str, topic: str) -> str:
    day = f"days/{date}"
    return (
        f"| {number} | {date} | {title} | {difficulty} | {topic} "
        f"| [code]({day}/{slug}.py) | O(?) | O(?) | ⏳ |"
    )


def add_progress_row(row: str, dry_run: bool) -> int | None:
    if not PROGRESS.exists():
        print("! progress.md not found — skipped the tracker update")
        return None
    text = PROGRESS.read_text(encoding="utf-8")
    if PROGRESS_MARKER not in text:
        print(f"! marker {PROGRESS_MARKER} missing — tracker left untouched")
        return None
    updated = text.replace(PROGRESS_MARKER, f"{row}\n{PROGRESS_MARKER}", 1)
    if not dry_run:
        PROGRESS.write_text(updated, encoding="utf-8")
    return 1


def build_day_readme(date: str) -> str:
    template = TEMPLATES / "day_readme_template.md"
    body = template.read_text(encoding="utf-8") if template.exists() else "# Day — {date}\n"
    return body.replace("YYYY-MM-DD", date)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a new problem folder + tracker row.")
    parser.add_argument("title", help='problem title, e.g. "Trapping Rain Water"')
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--difficulty", default="Medium", choices=["Easy", "Medium", "Hard"])
    parser.add_argument("--topic", default="TODO", help='e.g. "Two Pointers"')
    parser.add_argument("--dry-run", action="store_true", help="show what would be created")
    args = parser.parse_args(argv)

    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        parser.error(f"--date must be YYYY-MM-DD, got {args.date!r}")

    slug = slugify(args.title)
    day_dir = DAYS_DIR / args.date
    solution_path = day_dir / f"{slug}.py"
    test_path = day_dir / f"test_{slug}.py"
    readme_path = day_dir / "README.md"

    files = {
        solution_path: SOLUTION_STUB.format(
            title=args.title, difficulty=args.difficulty
        ),
        test_path: TEST_STUB.format(title=args.title, slug=slug, title_class=camel_case(args.title)),
        readme_path: build_day_readme(args.date),
    }

    for path, content in files.items():
        if path.exists():
            print(f"= skip {path.relative_to(ROOT)} (already exists)")
            continue
        print(f"+ {'would create' if args.dry_run else 'create'} {path.relative_to(ROOT)}")
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    text = PROGRESS.read_text(encoding="utf-8") if PROGRESS.exists() else ""
    row = progress_row(next_row_number(text), args.date, args.title, slug, args.difficulty, args.topic)
    print(f"+ {'would add' if args.dry_run else 'add'} tracker row: {row}")
    add_progress_row(row, args.dry_run)

    print("\nNext: fill in the approach, write tests, then run `python scripts/run_tests.py`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
