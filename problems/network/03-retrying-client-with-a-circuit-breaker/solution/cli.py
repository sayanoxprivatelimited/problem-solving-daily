"""CLI: replay a flaky dependency and show what the policy and breaker did.

    python -m solution.cli --failures 5 --threshold 2 --reset 5
    python -m solution.cli --failures 1 --json
"""
from __future__ import annotations

import argparse
import json

from solution.main import (
    CircuitBreaker,
    CircuitOpen,
    FakeClock,
    RetriesExhausted,
    RetryPolicy,
    RetryingClient,
    TransientError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retrying-client",
        description="Show retry backoff and circuit-breaker behaviour for one call.",
    )
    parser.add_argument("--failures", type=int, default=2, help="how many calls fail first")
    parser.add_argument("--attempts", type=int, default=4, help="retry policy attempts")
    parser.add_argument("--base-delay", type=float, default=0.1, help="first backoff (s)")
    parser.add_argument("--threshold", type=int, default=3, help="failures before the breaker opens")
    parser.add_argument("--reset", type=float, default=30.0, help="breaker cool-down (s)")
    parser.add_argument("--calls", type=int, default=2, help="how many top-level calls to make")
    parser.add_argument("--json", action="store_true", help="print the transcript as JSON")
    return parser


def simulate(args: argparse.Namespace) -> dict[str, object]:
    clock = FakeClock()
    sleeps: list[float] = []
    breaker = CircuitBreaker(clock, failure_threshold=args.threshold, reset_timeout=args.reset)
    client = RetryingClient(
        breaker,
        policy=RetryPolicy(attempts=args.attempts, base_delay=args.base_delay),
        clock=clock,
        sleeper=sleeps.append,
    )

    remaining = {"left": args.failures}
    outcomes: list[str] = []

    def flaky() -> str:
        if remaining["left"] > 0:
            remaining["left"] -= 1
            raise TransientError("upstream hiccup")
        return "response"

    for _ in range(args.calls):
        try:
            outcomes.append(f"ok: {client.call(flaky)}")
        except CircuitOpen as error:
            outcomes.append(f"short-circuited, retry in {error.retry_in:.1f}s")
        except RetriesExhausted as error:
            outcomes.append(f"exhausted: {error}")

    return {
        "outcomes": outcomes,
        "sleeps": sleeps,
        "breaker": breaker.state.value,
        "stats": client.stats,
    }


def render(report: dict[str, object]) -> str:
    lines = ["calls:"]
    lines += [f"  {outcome}" for outcome in report["outcomes"]]
    lines.append(f"backoff schedule: {report['sleeps']}")
    lines.append(f"breaker: {report['breaker']}")
    stats = report["stats"]
    lines.append(
        f"attempts {stats['attempts']}, retries {stats['retries']}, "
        f"slept {stats['slept']:.3f}s, short-circuited {stats['short_circuited']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.failures < 0 or args.attempts < 1 or args.calls < 1:
        print("error: --failures must be >= 0, --attempts and --calls >= 1")
        return 2

    report = simulate(args)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if any(outcome.startswith("ok") for outcome in report["outcomes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
