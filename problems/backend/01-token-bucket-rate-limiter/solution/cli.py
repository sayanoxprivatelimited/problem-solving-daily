"""Demo CLI: watch a burst get throttled, one line per request.

    python -m solution.cli --capacity 5 --rate 1 --requests 10 --interval 0.25
"""
from __future__ import annotations

import argparse
import time

from solution.main import RateLimiter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rate-limiter-demo",
        description="Simulate a burst of requests against a token bucket limiter.",
    )
    parser.add_argument("--capacity", type=float, default=5.0, help="burst size")
    parser.add_argument("--rate", type=float, default=1.0, help="tokens refilled per second")
    parser.add_argument("--requests", type=int, default=10, help="how many requests to send")
    parser.add_argument("--interval", type=float, default=0.2, help="seconds between requests")
    parser.add_argument("--cost", type=float, default=1.0, help="tokens per request")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.requests < 1 or args.interval < 0 or args.capacity < 0 or args.rate < 0:
        print("error: requests must be >= 1 and capacity/rate/interval >= 0")
        return 2

    limiter = RateLimiter(args.capacity, args.rate)
    allowed = 0
    print(f"{'#':>3}  {'status':<8} {'remaining':>10} {'retry_after':>12}")
    for index in range(1, args.requests + 1):
        decision = limiter.check("demo-client", cost=args.cost)
        allowed += decision.allowed
        status = "ok" if decision.allowed else "throttled"
        retry = "inf" if decision.retry_after == float("inf") else f"{decision.retry_after:.2f}s"
        print(f"{index:>3}  {status:<8} {decision.remaining:>10.2f} {retry:>12}")
        if index < args.requests and args.interval:
            time.sleep(args.interval)

    print(f"\nallowed {allowed}/{args.requests}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
