#!/usr/bin/env python3
"""Regenerate `catalog.md` and the 3D site's data file from the problem briefs.

Both outputs are generated, never hand-edited: `problem.md` is the single source of
truth for title, category, level, concepts and status. The script also validates the
repository layout and reports anything that looks half-finished.

    python scripts/build_catalog.py           # rewrite catalog.md + site data
    python scripts/build_catalog.py --check   # exit 1 if either file is stale

The site (`site/`) imports `site/data/catalog.js`, so the showcase can never
advertise a problem that does not exist or a test count that is wrong.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBLEMS_DIR = ROOT / "problems"
CATALOG = ROOT / "catalog.md"
SITE_DATA = ROOT / "site" / "data" / "problems.json"
SITE_MODULE = ROOT / "site" / "data" / "catalog.js"

REPOSITORY_URL = "https://github.com/sayanoxprivatelimited/problem-solving-daily"
DEFAULT_BRANCH = "main"

TEST_DEFINITION = re.compile(r"^\s*def test_\w+\(", re.MULTILINE)
DOCTEST_PROMPT = re.compile(r"^\s*>>> ", re.MULTILINE)

REQUIRED_KEYS = ("title", "category", "level", "concepts", "status")
LEVEL_BADGES = {"easy": "🟢 easy", "medium": "🟡 medium", "hard": "🔴 hard"}
STATUS_BADGES = {"solved": "✅ solved", "todo": "⏳ todo", "wip": "🚧 wip"}


@dataclass
class Problem:
    path: Path
    meta: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def category_dir(self) -> str:
        return self.path.parent.name

    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        return self.meta.get("title", self.slug)

    def has_tests(self) -> bool:
        return any((self.path / "tests").glob("test_*.py"))

    def has_solution(self) -> bool:
        main = self.path / "solution" / "main.py"
        return main.is_file() and main.stat().st_size > 0

    @property
    def tests(self) -> int:
        """Number of test methods, counted the same way `run_tests.py` runs them."""
        return sum(
            len(TEST_DEFINITION.findall(path.read_text(encoding="utf-8")))
            for path in (self.path / "tests").glob("test_*.py")
        )

    @property
    def doctests(self) -> int:
        return sum(
            len(DOCTEST_PROMPT.findall(path.read_text(encoding="utf-8")))
            for path in (self.path / "solution").glob("*.py")
        )

    @property
    def solution_lines(self) -> int:
        return sum(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in (self.path / "solution").glob("*.py")
        )

    @property
    def concepts(self) -> list[str]:
        return [item.strip() for item in self.meta.get("concepts", "").split(",") if item.strip()]

    def blob_url(self, relative_path: str) -> str:
        return f"{REPOSITORY_URL}/blob/{DEFAULT_BRANCH}/{self.path.relative_to(ROOT)}/{relative_path}"


def parse_front_matter(text: str) -> dict[str, str]:
    """Read the tiny `key: value` block between --- markers (no YAML dependency)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def load_problems() -> list[Problem]:
    problems: list[Problem] = []
    if not PROBLEMS_DIR.is_dir():
        return problems

    for path in sorted(p for p in PROBLEMS_DIR.glob("*/*") if p.is_dir()):
        problem = Problem(path=path)
        brief = path / "problem.md"
        if not brief.is_file():
            problem.warnings.append("missing problem.md")
        else:
            problem.meta = parse_front_matter(brief.read_text(encoding="utf-8"))
            missing = [key for key in REQUIRED_KEYS if key not in problem.meta]
            if missing:
                problem.warnings.append("front matter missing: " + ", ".join(missing))

        if problem.meta.get("category") and problem.meta["category"] != problem.category_dir:
            problem.warnings.append(
                f"category says '{problem.meta['category']}' but folder is '{problem.category_dir}'"
            )
        if problem.meta.get("status") == "solved" and not problem.has_tests():
            problem.warnings.append("marked solved but has no tests")
        if problem.meta.get("status") == "solved" and not problem.has_solution():
            problem.warnings.append("marked solved but solution/main.py is empty")
        problems.append(problem)
    return problems


def render_catalog(problems: list[Problem]) -> str:
    total = len(problems)
    solved = sum(1 for p in problems if p.meta.get("status") == "solved")
    categories = sorted({p.category_dir for p in problems})

    lines = [
        "<!-- Generated by scripts/build_catalog.py — do not edit by hand. -->",
        "",
        "# 📚 Problem catalog",
        "",
        f"**{total}** problems · **{solved}** solved and tested · "
        f"**{len(categories)}** categories · every solved problem has a runnable CLI and a test suite.",
        "",
        "| # | Problem | Category | Level | Concepts | Solution | Status |",
        "|---|---------|----------|-------|----------|----------|--------|",
    ]

    for index, problem in enumerate(problems, start=1):
        meta = problem.meta
        brief = f"problems/{problem.category_dir}/{problem.slug}/problem.md"
        code = f"problems/{problem.category_dir}/{problem.slug}/solution/main.py"
        level = LEVEL_BADGES.get(meta.get("level", "").lower(), meta.get("level", "—"))
        status = STATUS_BADGES.get(meta.get("status", ""), meta.get("status", "⏳ todo"))
        lines.append(
            f"| {index} | [{problem.title}]({brief}) | `{problem.category_dir}` | {level} "
            f"| {meta.get('concepts', '—')} | [code]({code}) | {status} |"
        )

    lines += ["", "## Breakdown", "", "| Category | Problems | Solved |", "|----------|----------|--------|"]
    for category in categories:
        in_category = [p for p in problems if p.category_dir == category]
        solved_in_category = sum(1 for p in in_category if p.meta.get("status") == "solved")
        lines.append(f"| `{category}` | {len(in_category)} | {solved_in_category} |")

    lines += ["", "| Level | Problems |", "|-------|----------|"]
    for level in ("easy", "medium", "hard"):
        count = sum(1 for p in problems if p.meta.get("level", "").lower() == level)
        if count:
            lines.append(f"| {LEVEL_BADGES[level]} | {count} |")

    warnings = [
        (problem, warning) for problem in problems for warning in problem.warnings
    ]
    lines += ["", "## Repository checks", ""]
    if warnings:
        lines += [f"- ⚠️ `{p.category_dir}/{p.slug}`: {w}" for p, w in warnings]
    else:
        lines.append("- ✅ every problem has front matter, a solution and tests")

    lines += [
        "",
        "## How to run everything",
        "",
        "```bash",
        "python scripts/run_tests.py          # all tests + doctests",
        "python scripts/build_catalog.py      # regenerate this file",
        "```",
        "",
    ]
    return "\n".join(lines)


def site_payload(problems: list[Problem]) -> dict:
    """Stats, categories and one entry per problem — the site's only data source."""
    levels = {level: 0 for level in ("easy", "medium", "hard")}
    for problem in problems:
        level = problem.meta.get("level", "").lower()
        if level in levels:
            levels[level] += 1

    categories = []
    for name in sorted({problem.category_dir for problem in problems}):
        in_category = [problem for problem in problems if problem.category_dir == name]
        categories.append(
            {
                "name": name,
                "problems": len(in_category),
                "solved": sum(1 for p in in_category if p.meta.get("status") == "solved"),
            }
        )

    payload = {
        "generated_by": "scripts/build_catalog.py — do not edit by hand",
        "repository": {"url": REPOSITORY_URL, "branch": DEFAULT_BRANCH},
        "stats": {
            "problems": len(problems),
            "solved": sum(1 for p in problems if p.meta.get("status") == "solved"),
            "categories": len(categories),
            "tests": sum(problem.tests for problem in problems),
            "doctests": sum(problem.doctests for problem in problems),
            "solution_lines": sum(problem.solution_lines for problem in problems),
            "levels": levels,
        },
        "categories": categories,
        "problems": [
            {
                "index": index,
                "slug": problem.slug,
                "category": problem.category_dir,
                "title": problem.title,
                "level": problem.meta.get("level", "medium"),
                "status": problem.meta.get("status", "todo"),
                "concepts": problem.concepts,
                "tests": problem.tests,
                "doctests": problem.doctests,
                "solution_lines": problem.solution_lines,
                "path": str(problem.path.relative_to(ROOT)),
                "brief_url": problem.blob_url("problem.md"),
                "code_url": problem.blob_url("solution/main.py"),
            }
            for index, problem in enumerate(problems, start=1)
        ],
    }
    return payload


def render_site_data(problems: list[Problem]) -> str:
    """`site/data/problems.json` — the same payload, for humans and for tooling."""
    return json.dumps(site_payload(problems), indent=2, ensure_ascii=False) + "\n"


def render_site_module(problems: list[Problem]) -> str:
    """`site/data/catalog.js` — the payload as an ES module the page imports.

    The page imports its data instead of fetching it: no request to fail, no
    "loading" state, and the site also works when opened straight from disk.
    """
    body = json.dumps(site_payload(problems), indent=2, ensure_ascii=False)
    return (
        "// Generated by scripts/build_catalog.py — do not edit by hand.\n"
        "// Mirrors site/data/problems.json; the page imports this module.\n"
        f"export default {body};\n"
    )


def build(check: bool = False) -> int:
    problems = load_problems()
    if not problems:
        print("no problems found under problems/")
        return 1

    rendered_catalog = render_catalog(problems)
    rendered_site = render_site_data(problems)
    rendered_module = render_site_module(problems)
    stale_catalog = not CATALOG.exists() or CATALOG.read_text(encoding="utf-8") != rendered_catalog
    stale_site = not SITE_DATA.exists() or SITE_DATA.read_text(encoding="utf-8") != rendered_site
    stale_module = (
        not SITE_MODULE.exists() or SITE_MODULE.read_text(encoding="utf-8") != rendered_module
    )

    if check:
        problems_found = []
        if stale_catalog:
            problems_found.append("catalog.md")
        if stale_site:
            problems_found.append("site/data/problems.json")
        if stale_module:
            problems_found.append("site/data/catalog.js")
        if problems_found:
            print(
                "out of date: " + ", ".join(problems_found)
                + " — run: python scripts/build_catalog.py"
            )
            return 1
        print(f"catalog.md and site data are up to date ({len(problems)} problems)")
        return 0

    CATALOG.write_text(rendered_catalog, encoding="utf-8")
    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    SITE_DATA.write_text(rendered_site, encoding="utf-8")
    SITE_MODULE.write_text(rendered_module, encoding="utf-8")
    warnings = sum(len(problem.warnings) for problem in problems)
    print(
        f"wrote catalog.md, site/data/problems.json + site/data/catalog.js"
        f" — {len(problems)} problem(s), {warnings} warning(s)"
    )
    for problem in problems:
        for warning in problem.warnings:
            print(f"  ⚠️  {problem.category_dir}/{problem.slug}: {warning}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate catalog.md and site/data/problems.json from problem briefs."
    )
    parser.add_argument(
        "--check", action="store_true", help="exit 1 when the generated files are stale"
    )
    args = parser.parse_args(argv)
    return build(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
