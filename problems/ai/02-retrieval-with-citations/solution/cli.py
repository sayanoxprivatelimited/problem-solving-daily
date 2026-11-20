"""CLI: index a folder of docs and answer a query with citations.

    python -m solution.cli tests/fixtures/docs --query "how do I rotate an api key" --k 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.main import Retriever, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docs-retriever", description="Rank documentation chunks for a query, with citations."
    )
    parser.add_argument("folder", help="folder of .md/.txt documents")
    parser.add_argument("--query", required=True, help="the user's question")
    parser.add_argument("--k", type=int, default=3, help="how many passages to return")
    parser.add_argument("--min-score", type=float, default=0.0, help="drop hits below this score")
    parser.add_argument("--chunk-size", type=int, default=200, help="characters per chunk")
    parser.add_argument("--overlap", type=int, default=40, help="characters of overlap")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"error: not a directory: {folder}", file=sys.stderr)
        return 1

    retriever = Retriever()
    try:
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
                retriever.add_document(
                    path.name,
                    path.read_text(encoding="utf-8"),
                    size=args.chunk_size,
                    overlap=args.overlap,
                )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if retriever.chunk_count == 0:
        print(f"error: no .md/.txt documents in {folder}", file=sys.stderr)
        return 1

    hits = retriever.search(args.query, k=args.k, min_score=args.min_score)
    print(f"indexed {retriever.chunk_count} chunk(s) from {len(retriever.document_ids)} document(s)\n")
    print(render(args.query, hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
