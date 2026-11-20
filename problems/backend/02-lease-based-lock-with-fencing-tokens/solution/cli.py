"""CLI: show the stalled-writer story, or inspect one lock in a single line.

    python -m solution.cli --lease 10 --stall 15
    python -m solution.cli --lease 10 --stall 15 --json
"""
from __future__ import annotations

import argparse
import json

from solution.main import FakeClock, FenceGuard, LeaseLock, StaleTokenError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fenced-lock",
        description="Demonstrate why a lease needs a fencing token.",
    )
    parser.add_argument("--lease", type=float, default=10.0, help="lease length in seconds")
    parser.add_argument(
        "--stall",
        type=float,
        default=15.0,
        help="how long the first worker stalls before it tries to write (seconds)",
    )
    parser.add_argument("--json", action="store_true", help="print the transcript as JSON")
    return parser


def simulate(lease: float, stall: float) -> dict[str, object]:
    """Run the two-worker story and return what happened, as data."""
    clock = FakeClock()
    lock = LeaseLock(clock, lease=lease, name="invoice-42")
    guard = FenceGuard()

    first = lock.acquire("worker-a")
    guard.write(first, "v1 by worker-a")

    clock.advance(stall)  # worker-a freezes; its lease quietly expires
    expired = first.is_expired(clock)

    second = lock.try_acquire("worker-b")
    if second is None:
        # The lease is still live, so the second worker never gets in. No unfairness,
        # no lost write — the boring case is the correct one.
        return {
            "lease": lease,
            "stall": stall,
            "first": {"owner": first.owner, "fence": first.fence, "expired": expired},
            "second": None,
            "applied": [fence for fence, _ in guard.writes],
            "value": guard.value,
            "stale_rejected": False,
            "rejection": "lease still live; worker-b stayed outside",
        }

    guard.write(second, "v2 by worker-b")

    stale_rejected = False
    rejection = ""
    try:
        guard.write(first, "v1 by worker-a (late)")
    except StaleTokenError as error:
        stale_rejected = True
        rejection = str(error)

    return {
        "lease": lease,
        "stall": stall,
        "first": {"owner": first.owner, "fence": first.fence, "expired": expired},
        "second": {"owner": second.owner, "fence": second.fence},
        "applied": [fence for fence, _ in guard.writes],
        "value": guard.value,
        "stale_rejected": stale_rejected,
        "rejection": rejection,
    }


def render(report: dict[str, object]) -> str:
    first = report["first"]
    second = report["second"]
    if second is None:
        return "\n".join(
            [
                f"lease {report['lease']}s, worker-a stalls {report['stall']}s",
                f"  worker-a acquired fence {first['fence']} and wrote",
                f"  worker-a's lease expired: {first['expired']}",
                f"  worker-b could not acquire: {report['rejection']}",
                f"  applied fences: {report['applied']}",
            ]
        )
    lines = [
        f"lease {report['lease']}s, worker-a stalls {report['stall']}s",
        f"  worker-a acquired fence {first['fence']} and wrote",
        f"  worker-a's lease expired: {first['expired']}",
        f"  worker-{second['owner'].split('-')[-1]} acquired fence {second['fence']} and wrote",
        f"  applied fences: {report['applied']}",
        f"  write now holds: {report['value']!r}",
        f"  worker-a's late write rejected: {report['stale_rejected']}"
        + (f" ({report['rejection']})" if report["rejection"] else ""),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.lease <= 0:
        print("error: --lease must be positive")
        return 2
    if args.stall < 0:
        print("error: --stall cannot be negative")
        return 2

    report = simulate(args.lease, args.stall)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    return 0 if report["stale_rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
