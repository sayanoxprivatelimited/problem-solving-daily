"""CLI: replay an idempotent operation and show what the store did.

    python -m solution.cli --key charge-1 --payload '{"amount": 10}' --calls 3
    python -m solution.cli --key charge-2 --payload '{"amount": 10}' --fail-once --json

The simulated handler counts its own executions, so the output shows the difference
between "executed" (the side effect ran) and "replayed" (the answer came from the
store) — the whole point of the exercise.

Exit codes: 0 every call agreed, 1 a conflict was detected, 2 the input was unusable.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from solution.main import (
    FakeClock,
    IdempotencyConflict,
    IdempotencyError,
    IdempotencyLayer,
    MemoryStore,
    RequestInFlight,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idempotent", description="Replay a request under one idempotency key."
    )
    parser.add_argument("--key", required=True, help="the client's idempotency key")
    parser.add_argument("--payload", default="{}", help="request body as JSON")
    parser.add_argument("--calls", type=int, default=2, help="how many times to send it")
    parser.add_argument(
        "--ttl-ms", type=int, default=24 * 60 * 60 * 1000, help="how long the record lives"
    )
    parser.add_argument(
        "--fail-once", action="store_true", help="make the first execution raise"
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object per call")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.calls < 1:
        print("error: --calls must be >= 1", file=sys.stderr)
        return 2
    try:
        payload: Any = json.loads(args.payload)
    except json.JSONDecodeError as error:
        print(f"error: --payload is not JSON ({error.msg})", file=sys.stderr)
        return 2

    clock = FakeClock(1_700_000_000_000)
    layer = IdempotencyLayer(MemoryStore(clock), clock, ttl_ms=args.ttl_ms)

    state = {"executions": 0, "failed_once": False}

    def handler(body: Any) -> tuple[int, str]:
        if args.fail_once and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("downstream timeout")
        state["executions"] += 1
        return 201, json.dumps({"charged": body, "execution": state["executions"]}, sort_keys=True)

    rows: list[dict[str, Any]] = []
    exit_code = 0

    for call in range(1, args.calls + 1):
        try:
            result = layer.run(args.key, payload, handler)
        except IdempotencyConflict as error:
            rows.append({"call": call, "outcome": "conflict", "detail": str(error)})
            exit_code = 1
        except RequestInFlight as error:
            rows.append({"call": call, "outcome": "in-flight", "detail": str(error)})
        except RuntimeError as error:
            rows.append({"call": call, "outcome": "failed", "detail": str(error)})
        except IdempotencyError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        else:
            rows.append(
                {
                    "call": call,
                    "outcome": "replayed" if result.replayed else "executed",
                    "status": result.status,
                    "body": result.body,
                }
            )

    if args.json:
        for row in rows:
            print(json.dumps(row, sort_keys=True))
    else:
        print(f"{'call':<5} {'outcome':<9} {'status':<7} body")
        for row in rows:
            print(
                f"{row['call']:<5} {row['outcome']:<9} {row.get('status', '-'):<7} "
                f"{row.get('body', row.get('detail', ''))}"
            )

    stats = layer.stats
    print(
        f"\nexecutions: {state['executions']} · executed {stats['executed']} · "
        f"replayed {stats['replayed']} · conflicts {stats['conflicts']} · "
        f"in-flight {stats['rejected']} · stored {len(layer.store)} key(s)",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
