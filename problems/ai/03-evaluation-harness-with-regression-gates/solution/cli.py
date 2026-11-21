"""CLI: score a run against absolute floors and, optionally, a baseline.

    python -m solution.cli candidate.json --min-precision 0.8
    python -m solution.cli candidate.json --baseline baseline.json --max-drop 0.01
    python -m solution.cli candidate.json --max-cost-per-1k 0.5 --json

Exit codes: 0 the gate passed, 1 the gate failed, 2 the input was unusable.
"""
from __future__ import annotations

import argparse
import json
import sys

from solution.main import EvaluationError, Gate, evaluate, load_json_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval-gate", description="Score an eval run and fail the build on a regression."
    )
    parser.add_argument("candidate", help="JSON file with the run under test")
    parser.add_argument("--baseline", help="JSON file with the run to compare against")
    parser.add_argument("--min-precision", type=float, metavar="F", help="absolute floor, 0..1")
    parser.add_argument("--min-recall", type=float, metavar="F", help="absolute floor, 0..1")
    parser.add_argument(
        "--max-cost-per-1k", type=float, metavar="USD", help="budget for 1,000 cases"
    )
    parser.add_argument(
        "--max-drop", type=float, default=0.0, metavar="F", help="tolerated drop vs baseline"
    )
    parser.add_argument(
        "--max-cost-increase",
        type=float,
        default=0.0,
        metavar="FRACTION",
        help="tolerated cost increase vs baseline, e.g. 0.1 for 10%%",
    )
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        candidate = load_json_file(args.candidate)
        baseline = load_json_file(args.baseline) if args.baseline else None
        gate = Gate(
            min_precision=args.min_precision,
            min_recall=args.min_recall,
            max_cost_per_1k=args.max_cost_per_1k,
            max_drop=args.max_drop,
            max_cost_increase=args.max_cost_increase,
        )
    except EvaluationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        candidate_score = evaluate(candidate)
        baseline_score = evaluate(baseline) if baseline else None
    except EvaluationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    verdict = gate.check(candidate_score, baseline=baseline_score)

    if args.json:
        print(
            json.dumps(
                {
                    "candidate": candidate_score.as_dict(),
                    "baseline": baseline_score.as_dict() if baseline_score else None,
                    "passed": verdict.passed,
                    "failures": list(verdict.failures),
                    "checked": list(verdict.checked),
                },
                indent=2,
            )
        )
    else:
        print(_row("candidate", candidate_score))
        if baseline_score:
            print(_row("baseline", baseline_score))
        print()
        print(verdict.render())

    return 0 if verdict.passed else 1


def _row(label: str, score) -> str:
    return (
        f"{label:<10} precision {score.precision:.3f}  recall {score.recall:.3f}  "
        f"f1 {score.f1:.3f}  exact {score.accuracy:.3f}  "
        f"cost/1k {score.cost_per_1k:.3f}  ({score.cases} cases)"
    )


if __name__ == "__main__":
    raise SystemExit(main())
