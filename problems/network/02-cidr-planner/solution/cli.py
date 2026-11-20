"""CLI: inspect a CIDR, split it, or check a list of allocations for overlaps.

    python -m solution.cli 10.0.0.0/16 --split 4
    python -m solution.cli 10.0.0.0/16 --check 10.0.1.0/24 10.0.1.128/25
"""
from __future__ import annotations

import argparse
import sys

from solution.main import IPv4Network, plan_subnets, summarize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cidr-planner", description="IPv4 subnet arithmetic and allocation checks."
    )
    parser.add_argument("cidr", help="base network, e.g. 10.0.0.0/16")
    parser.add_argument("--split", type=int, metavar="N", help="split into N equal subnets")
    parser.add_argument(
        "--check", nargs="+", metavar="CIDR", help="validate allocations inside the base"
    )
    parser.add_argument("--summarize", action="store_true", help="collapse the checked list")
    return parser


def report(network: IPv4Network) -> str:
    first, last = network.host_range
    return (
        f"network      {network.network_text}/{network.prefix}\n"
        f"netmask      {network.netmask}\n"
        f"wildcard     {network.wildcard}\n"
        f"broadcast    {network.broadcast_text}\n"
        f"host range   {first} - {last}\n"
        f"usable hosts {network.usable_hosts}\n"
        f"private      {network.is_private}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        base = IPv4Network.parse(args.cidr)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(report(base))

    if args.split:
        try:
            subnets = base.split(args.split)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"\n{len(subnets)} subnet(s):")
        for subnet in subnets:
            first, last = subnet.host_range
            print(f"  {str(subnet):<20} {first:<15} - {last:<15} {subnet.usable_hosts:>6} hosts")

    if args.check:
        try:
            plan = plan_subnets(base, args.check)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print("\nplan:")
        print(plan)
        if args.summarize:
            print("\nsummarised: " + ", ".join(str(network) for network in summarize(plan.allocations)))
        if not plan.ok:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
