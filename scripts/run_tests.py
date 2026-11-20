#!/usr/bin/env python3
"""Run every test in this repository — standard library only, no pip install.

Usage
-----
    python scripts/run_tests.py            # unit tests + doctests
    python scripts/run_tests.py -v         # per-test output
    python scripts/run_tests.py --doctests # only the docstring examples

Why not `python -m unittest discover`?
-------------------------------------
Day folders are named `days/2026-11-20`, and a dash is not valid in a Python
module name, so ordinary discovery cannot import them as packages.  Instead we
load each `test_*.py` file straight from its path, under a unique module name,
and additionally run the `>>>` examples inside every solution file's docstring.
"""
from __future__ import annotations

import argparse
import doctest
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
DAYS_DIR = ROOT / "days"


def _module_name(path: Path, prefix: str) -> str:
    day = path.parent.name.replace("-", "_")
    return f"{prefix}_{day}_{path.stem}"


def load_module(path: Path, prefix: str) -> ModuleType:
    """Import a .py file by path, making its folder importable for siblings."""
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)

    name = _module_name(path, prefix)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def day_files(pattern: str) -> list[Path]:
    if not DAYS_DIR.is_dir():
        return []
    return sorted(p for p in DAYS_DIR.glob(f"*/{pattern}") if p.is_file())


def run_unit_tests(verbose: bool) -> unittest.TestResult:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    paths = day_files("test_*.py")
    for path in paths:
        suite.addTests(loader.loadTestsFromModule(load_module(path, "tests")))
    print(f"Unit tests — {len(paths)} file(s), {suite.countTestCases()} test(s)")
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1, buffer=not verbose)
    return runner.run(suite)


def run_doctests() -> tuple[int, int]:
    """Return (examples run, failures) across all non-test solution files."""
    total_run = 0
    total_failed = 0
    for path in day_files("*.py"):
        if path.name.startswith("test_"):
            continue
        module = load_module(path, "doctests")
        result = doctest.testmod(module, verbose=False)
        if result.attempted:
            status = "ok" if result.failed == 0 else f"{result.failed} FAILED"
            print(f"  doctest {path.relative_to(ROOT)}: {result.attempted} example(s) {status}")
        total_run += result.attempted
        total_failed += result.failed
    return total_run, total_failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all unit tests and doctests.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show each test")
    parser.add_argument(
        "--doctests", action="store_true", help="run only the docstring examples"
    )
    args = parser.parse_args(argv)

    failures = 0

    print("Doctests —", "running")
    run_count, failed = run_doctests()
    print(f"Doctests: {run_count} example(s), {failed} failure(s)\n")
    failures += failed

    if not args.doctests:
        result = run_unit_tests(args.verbose)
        if not result.wasSuccessful():
            failures += len(result.failures) + len(result.errors)

    print()
    if failures:
        print(f"❌ {failures} failure(s)")
        return 1
    print("✅ All green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
