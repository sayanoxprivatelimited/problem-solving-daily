#!/usr/bin/env python3
"""Scaffold a new problem folder and refresh the catalog.

Examples
--------
    python scripts/new_problem.py data "Retry with exponential backoff"
    python scripts/new_problem.py backend "Idempotency keys" --level hard \
        --concepts "HTTP, storage, concurrency"
    python scripts/new_problem.py cli "Git hook installer" --dry-run

Creates
  problems/<category>/NN-<slug>/problem.md
  problems/<category>/NN-<slug>/solution/{__init__.py,main.py}
  problems/<category>/NN-<slug>/tests/{__init__.py,test_main.py,fixtures/.gitkeep}

Nothing is overwritten, and the catalog is regenerated at the end.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
PROBLEMS_DIR = ROOT / "problems"
TEMPLATES_DIR = ROOT / "templates"

sys.path.insert(0, str(ROOT / "scripts"))
import build_catalog  # noqa: E402  (needs the path tweak above)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot build a folder name from {title!r}")
    return slug


def class_name(title: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", title)
    name = "".join(part.capitalize() for part in parts)
    return f"Test{name or 'Problem'}"


def categories() -> list[str]:
    if not PROBLEMS_DIR.is_dir():
        return []
    return sorted(path.name for path in PROBLEMS_DIR.iterdir() if path.is_dir())


def next_number(category: str) -> int:
    numbers = []
    for path in (PROBLEMS_DIR / category).glob("[0-9]*-*"):
        match = re.match(r"(\d+)-", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def render(template_name: str, **values: str) -> str:
    return Template((TEMPLATES_DIR / template_name).read_text(encoding="utf-8")).substitute(values)


def main(argv: list[str] | None = None) -> int:
    available = categories()
    parser = argparse.ArgumentParser(description="Scaffold a new real-world problem.")
    parser.add_argument("category", help=f"one of: {', '.join(available) or '(none yet)'}")
    parser.add_argument("title", help='problem title, e.g. "Retry with exponential backoff"')
    parser.add_argument("--level", default="medium", choices=["easy", "medium", "hard"])
    parser.add_argument("--concepts", default="TODO")
    parser.add_argument("--status", default="todo", choices=["todo", "wip", "solved"])
    parser.add_argument("--dry-run", action="store_true", help="show what would be created")
    args = parser.parse_args(argv)

    if args.category not in available:
        print(f"error: unknown category {args.category!r}", file=sys.stderr)
        print(f"       available: {', '.join(available) or '(none)'}", file=sys.stderr)
        print("       add a folder under problems/ first, e.g. mkdir problems/ml", file=sys.stderr)
        return 2

    slug = slugify(args.title)
    problem_dir = PROBLEMS_DIR / args.category / f"{next_number(args.category):02d}-{slug}"
    values = {
        "title": args.title,
        "category": args.category,
        "level": args.level,
        "concepts": args.concepts,
        "status": args.status,
        "slug": problem_dir.name,
        "class_name": class_name(args.title),
    }

    files: dict[Path, str] = {
        problem_dir / "problem.md": render("problem_template.md", **values),
        problem_dir / "solution" / "__init__.py": f'"""Solution package for the {args.title} problem."""\n',
        problem_dir / "solution" / "main.py": render("module_template.py", **values),
        problem_dir / "tests" / "__init__.py": f'"""Tests for the {args.title} problem."""\n',
        problem_dir / "tests" / "test_main.py": render("test_template.py", **values),
        problem_dir / "tests" / "fixtures" / ".gitkeep": "",
    }

    for path, content in files.items():
        relative = path.relative_to(ROOT)
        if path.exists():
            print(f"= skip {relative}")
            continue
        print(f"+ {'would create' if args.dry_run else 'create'} {relative}")
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    if args.dry_run:
        print("\n(dry run — catalog.md not touched)")
        return 0

    build_catalog.build()
    print(f"\nNext: edit {problem_dir.relative_to(ROOT)}/problem.md, then run")
    print(f"  cd {problem_dir.relative_to(ROOT)} && python -m unittest discover -s tests -t .")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
