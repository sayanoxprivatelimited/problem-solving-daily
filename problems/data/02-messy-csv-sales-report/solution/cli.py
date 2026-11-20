"""CLI entry point: `python -m solution.cli sample_sales.csv [--json] [--rejects-out f]`."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from solution.main import clean_file, monthly_report, render, write_rejections


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sales-report",
        description="Clean a messy POS sales export and report monthly revenue.",
    )
    parser.add_argument("csvfile", help="path to the sales export")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--rejects-out", metavar="PATH", help="write refused rows (with reasons) to CSV"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = clean_file(args.csvfile)
    except OSError as exc:
        print(f"error: cannot read {args.csvfile}: {exc}", file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(f"error: {args.csvfile} is not UTF-8 text: {exc}", file=sys.stderr)
        return 1

    if args.rejects_out:
        written = write_rejections(args.rejects_out, result.rejected)
        print(f"wrote {written} rejected row(s) to {Path(args.rejects_out)}", file=sys.stderr)

    if args.json:
        payload = {
            "sales": [
                {**asdict(sale), "date": sale.date.isoformat(), "amount": str(sale.amount)}
                for sale in result.sales
            ],
            "rejected": [
                {"row": dict(item.row), "reason": item.reason} for item in result.rejected
            ],
            "duplicates": list(result.duplicates),
            "monthly": [
                {
                    "month": item.month,
                    "currency": item.currency,
                    "total": str(item.total),
                    "orders": item.orders,
                }
                for item in monthly_report(result.sales)
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
