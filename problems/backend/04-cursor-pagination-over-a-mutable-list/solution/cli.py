"""CLI: page through a JSON row list, one page or the whole walk.

    python -m solution.cli tests/fixtures/rows.json --page-size 2
    python -m solution.cli tests/fixtures/rows.json --page-size 2 --walk --json
    python -m solution.cli rows.json --page-size 2 --cursor <token>

Rows are `{"id": "...", "sort_key": 100, "payload": "..."}`; the file may be a JSON
list, an object with a `rows` list, or JSONL (one row per line).

Exit codes: 0 success, 1 the cursor was refused, 2 the input was unusable.
"""
from __future__ import annotations

import argparse
import json
import sys

from solution.main import CursorError, RowStore, gather, rows_from


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paginate", description="Keyset + snapshot pagination.")
    parser.add_argument("path", help="JSON list, object with 'rows', or JSONL file")
    parser.add_argument("--page-size", type=int, default=2, help="rows per page (default 2)")
    parser.add_argument("--cursor", help="continue from this cursor")
    parser.add_argument("--walk", action="store_true", help="follow next_cursor to the end")
    parser.add_argument("--secret", default="row-store-secret", help="cursor signing secret")
    parser.add_argument("--json", action="store_true", help="print JSON")
    return parser


def read_rows(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise ValueError(f"{path}: {error.strerror}") from error

    try:
        return rows_from(json.loads(text))
    except json.JSONDecodeError:
        lines = [line for line in text.splitlines() if line.strip()]
        try:
            return rows_from([json.loads(line) for line in lines])
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}: invalid JSON ({error.msg})") from error


def describe(page, index: int) -> str:
    ids = ", ".join(row.id for row in page.rows) or "(empty)"
    cursor = page.next_cursor or "None"
    return f"page {index}: {len(page)} row(s) [{ids}] snapshot {page.snapshot} next {cursor}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.page_size < 1:
        print("error: --page-size must be >= 1", file=sys.stderr)
        return 2

    try:
        rows = read_rows(args.path)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    store = RowStore(secret=args.secret.encode("utf-8"), max_page=max(args.page_size, 100))
    for row in rows:
        store.add(row)

    try:
        if args.walk:
            pages = list(store.walk(limit=args.page_size, cursor=args.cursor))
        else:
            pages = [store.page(cursor=args.cursor, limit=args.page_size)]
    except CursorError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "rows": [
                            {"id": row.id, "sort_key": row.sort_key, "payload": row.payload}
                            for row in page.rows
                        ],
                        "snapshot": page.snapshot,
                        "next_cursor": page.next_cursor,
                    }
                    for page in pages
                ],
                indent=2,
            )
        )
    else:
        for index, page in enumerate(pages, start=1):
            print(describe(page, index))

    if args.walk:
        print(
            f"\n{len(pages)} page(s), {len(gather(pages))} row(s), snapshot {pages[0].snapshot if pages else 0}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
