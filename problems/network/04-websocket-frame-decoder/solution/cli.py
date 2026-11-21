"""CLI: decode WebSocket frames from hex or a file, optionally one byte at a time.

    python -m solution.cli --hex 8105 68656c6c6f
    python -m solution.cli --hex 880203e8 --json
    python -m solution.cli --file frames.bin --chunk 1 --require-masking

Exit codes: 0 the stream decoded, 1 a protocol error (the close code is printed),
2 the input was unusable.
"""
from __future__ import annotations

import argparse
import json
import sys

from solution.main import Close, Decoder, Event, ProtocolError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wsdecode", description="Decode RFC 6455 frames.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--hex", nargs="+", help="frame bytes as hex, spaces allowed")
    source.add_argument("--file", help="a file holding raw frame bytes")
    parser.add_argument("--chunk", type=int, default=0, help="feed this many bytes at a time")
    parser.add_argument(
        "--masking",
        choices=["both", "required", "forbidden"],
        default="both",
        help="which side of the connection are these frames from?",
    )
    parser.add_argument("--max-message", type=int, default=1024 * 1024)
    parser.add_argument("--json", action="store_true", help="print events as JSON")
    return parser


def parse_hex(parts: list[str]) -> bytes:
    cleaned = "".join(parts).replace(" ", "").replace("\n", "")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as error:
        raise ValueError(f"not valid hex: {error}") from error


def describe(event: Event) -> str:
    if event.kind == "text":
        return f"text {event.text!r}"  # type: ignore[attr-defined]
    if event.kind == "binary":
        return f"binary {len(event.data)} byte(s)"  # type: ignore[attr-defined]
    if event.kind in {"ping", "pong"}:
        return f"{event.kind} {len(event.data)} byte(s)"  # type: ignore[attr-defined]
    close = event
    assert isinstance(close, Close)
    code = "no code" if close.code is None else str(close.code)
    return f"close {code} {close.reason!r}".rstrip()


def to_dict(event: Event) -> dict[str, object]:
    if event.kind in {"text", "binary"}:
        return {"kind": event.kind, "data": getattr(event, "text", None) or event.data.hex()}  # type: ignore[attr-defined]
    if event.kind in {"ping", "pong"}:
        return {"kind": event.kind, "data": event.data.hex()}  # type: ignore[attr-defined]
    close = event
    assert isinstance(close, Close)
    return {"kind": "close", "code": close.code, "reason": close.reason}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.chunk < 0 or args.max_message < 1:
        print("error: --chunk must be >= 0 and --max-message >= 1", file=sys.stderr)
        return 2

    try:
        if args.hex is not None:
            raw = parse_hex(args.hex)
        else:
            with open(args.file, "rb") as handle:
                raw = handle.read()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return 2

    require_masking = {"both": None, "required": True, "forbidden": False}[args.masking]
    decoder = Decoder(require_masking=require_masking, max_message_size=args.max_message)

    step = args.chunk or max(len(raw), 1)
    events: list[Event] = []
    try:
        for start in range(0, len(raw), step):
            events.extend(decoder.feed(raw[start : start + step]))
    except ProtocolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except (TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([to_dict(event) for event in events], indent=2))
    else:
        for event in events:
            print(describe(event))
    print(
        f"{len(events)} event(s), {len(raw)} byte(s), {decoder.pending_bytes} byte(s) left over",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
