"""CLI: train a tokenizer on a corpus file and show what it does.

    python -m solution.cli --corpus tests/fixtures/corpus.txt --vocab-size 400 \
        --text "the tokenizer must round trip the same text"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.main import BPETokenizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bpe-tokenizer", description="Train a BPE tokenizer from scratch and try it."
    )
    parser.add_argument("--corpus", required=True, help="text file to train on")
    parser.add_argument("--vocab-size", type=int, default=400, help="hard vocabulary cap")
    parser.add_argument("--min-frequency", type=int, default=2, help="stop merging below this")
    parser.add_argument("--text", default="", help="text to encode once training is done")
    parser.add_argument(
        "--unknown-token", metavar="TOKEN", help="degrade unseen characters to this token"
    )
    parser.add_argument("--save", metavar="PATH", help="write the tokenizer JSON here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        corpus = Path(args.corpus).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"error: cannot read {args.corpus}: {exc}", file=sys.stderr)
        return 1

    try:
        tokenizer = BPETokenizer.train(
            corpus,
            args.vocab_size,
            min_frequency=args.min_frequency,
            unknown_token=args.unknown_token,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"vocab_size   {tokenizer.vocab_size}")
    print(f"merges       {len(tokenizer.merges)}")
    print("first merges " + ", ".join(f"{m.left}+{m.right}->{m.merged}" for m in tokenizer.merges[:8]))

    if args.text:
        try:
            tokens = tokenizer.tokenize(args.text)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("hint: pass --unknown-token <unk> to degrade unseen characters", file=sys.stderr)
            return 1
        ids = tokenizer.encode(args.text)
        print(f"\ntext         {args.text!r}")
        print(f"tokens       {tokens}")
        print(f"ids          {ids}")
        print(f"decoded      {tokenizer.decode(ids)!r}")
        print(f"lossless     {tokenizer.decode(ids) == args.text}")
        print(f"char/token   {tokenizer.compression_ratio(args.text):.2f}")

    if args.save:
        tokenizer.save(args.save)
        print(f"\nsaved        {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
