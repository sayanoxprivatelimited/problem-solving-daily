#!/usr/bin/env python3
"""Run every problem's tests and doctests from the repository root.

Usage
-----
    python scripts/run_tests.py              # everything
    python scripts/run_tests.py -v           # per-test output
    python scripts/run_tests.py --only data  # only problems whose path matches

Why not `python -m unittest discover`?
-------------------------------------
Every problem owns a package called `solution`, so a single discovery run would
import one problem's `solution.main` under the same module name as another's.
This runner gives each problem a clean import context (fresh `sys.modules` entry
plus its own folder on `sys.path`), which is also what lets a problem be run on
its own with `cd problems/<category>/<slug> && python -m unittest discover`.
"""
from __future__ import annotations

import argparse
import doctest
import importlib.util
import io
import sys
import unittest
from contextlib import contextmanager, redirect_stdout, suppress
from pathlib import Path
from types import ModuleType
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
PROBLEMS_DIR = ROOT / "problems"


def problem_dirs(only: str | None = None) -> list[Path]:
    """Every `problems/<category>/<slug>` folder that has a solution package."""
    found = sorted(
        path
        for path in PROBLEMS_DIR.glob("*/*")
        if path.is_dir() and (path / "solution").is_dir()
    )
    if only:
        found = [path for path in found if only in str(path.relative_to(ROOT))]
    return found


def _purge_solution_modules() -> None:
    for name in [n for n in sys.modules if n == "solution" or n.startswith("solution.")]:
        del sys.modules[name]


@contextmanager
def import_context(problem: Path) -> Iterator[None]:
    """Make `import solution.x` resolve to *this* problem's solution package."""
    _purge_solution_modules()
    sys.path.insert(0, str(problem))
    try:
        yield
    finally:
        with suppress(ValueError):
            sys.path.remove(str(problem))
        _purge_solution_modules()


def load_module(path: Path, prefix: str) -> ModuleType:
    """Import a file by path under a name unique to that file."""
    name = f"{prefix}_{path.parent.parent.parent.name}_{path.parent.parent.name}_{path.stem}"
    name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_doctests(problem: Path) -> tuple[int, int]:
    """Run the `>>>` examples in every solution module. Returns (examples, failures)."""
    examples = failures = 0
    for path in sorted((problem / "solution").glob("*.py")):
        if path.name == "__init__.py":
            continue
        buffer = io.StringIO()
        module = load_module(path, "doctest")
        with redirect_stdout(buffer):
            result = doctest.testmod(module, verbose=False, report=True)
        examples += result.attempted
        failures += result.failed
        if result.failed:
            print(buffer.getvalue().rstrip())
    return examples, failures


def run_problem(problem: Path, verbose: bool) -> tuple[int, int]:
    """Run one problem's tests. Returns (tests, failures + errors)."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    with import_context(problem):
        for test_path in sorted((problem / "tests").glob("test_*.py")):
            module = load_module(test_path, "tests")
            suite.addTests(loader.loadTestsFromModule(module))

        if verbose:
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        else:
            buffer = io.StringIO()
            result = unittest.TextTestRunner(verbosity=0, stream=buffer).run(suite)
            if not result.wasSuccessful():
                print(buffer.getvalue().rstrip())

    return suite.countTestCases(), len(result.failures) + len(result.errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all problem tests and doctests.")
    parser.add_argument("-v", "--verbose", action="store_true", help="one line per test")
    parser.add_argument("--only", metavar="SUBSTRING", help="only matching problem paths")
    args = parser.parse_args(argv)

    problems = problem_dirs(args.only)
    if not problems:
        print("no problems found")
        return 1

    total_tests = total_failures = total_examples = 0
    print(f"{'problem':<52} {'tests':>6}  {'doctests':>8}  result")
    print("-" * 84)

    for problem in problems:
        label = str(problem.relative_to(PROBLEMS_DIR))
        with import_context(problem):
            examples, example_failures = run_doctests(problem)
        tests, test_failures = run_problem(problem, args.verbose)
        failures = test_failures + example_failures

        total_tests += tests
        total_examples += examples
        total_failures += failures
        status = "ok" if failures == 0 else f"FAILED ({failures})"
        print(f"{label:<52} {tests:>6}  {examples:>8}  {status}")

    print("-" * 84)
    print(f"{len(problems)} problem(s), {total_tests} tests, {total_examples} doctests")
    if total_failures:
        print(f"❌ {total_failures} failure(s)")
        return 1
    print("✅ all green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
