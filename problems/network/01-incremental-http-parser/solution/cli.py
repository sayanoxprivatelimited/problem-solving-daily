"""CLI: parse a raw request file, feeding it in small chunks like a socket would.

    python -m solution.cli tests/fixtures/post.http --chunk-size 7
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.main import HttpRequestParser, ParseError, Request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="http-parser", description="Parse a raw HTTP/1.1 request from a file in chunks."
    )
    parser.add_argument("request", help="file containing the raw request bytes")
    parser.add_argument("--chunk-size", type=int, default=16, help="bytes handed over per feed()")
    return parser


def describe(request: Request) -> str:
    headers = "\n".join(f"  {name}: {value}" for name, value in request.headers.items())
    body = request.body.decode("utf-8", errors="replace")
    return (
        f"{request.method} {request.target} {request.version}\n"
        f"keep-alive: {request.keep_alive}\n"
        f"headers ({len(request.headers)}):\n{headers or '  (none)'}\n"
        f"body ({len(request.body)} bytes): {body!r}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.chunk_size < 1:
        print("error: --chunk-size must be >= 1", file=sys.stderr)
        return 2

    try:
        raw = Path(args.request).read_bytes()
    except OSError as exc:
        print(f"error: cannot read {args.request}: {exc}", file=sys.stderr)
        return 1

    parser = HttpRequestParser()
    requests = 0
    try:
        for offset in range(0, len(raw), args.chunk_size):
            chunk = raw[offset : offset + args.chunk_size]
            request = parser.feed(chunk)
            if request is not None:
                requests += 1
                print(f"--- request {requests} (complete after {offset + len(chunk)} bytes) ---")
                print(describe(request))
        trailing = parser.feed(b"")
        while trailing is not None:
            requests += 1
            print(f"--- request {requests} (pipelined) ---")
            print(describe(trailing))
            trailing = parser.feed(b"")
    except ParseError as exc:
        print(f"error: {exc.reason} (HTTP {exc.status})", file=sys.stderr)
        return 1

    if requests == 0:
        print(f"incomplete request: {parser.buffered_bytes} byte(s) buffered", file=sys.stderr)
        return 1

    print(f"\nparsed {requests} request(s), {parser.buffered_bytes} trailing byte(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
