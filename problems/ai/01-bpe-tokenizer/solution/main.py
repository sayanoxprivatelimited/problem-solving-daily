"""A byte-pair encoding (BPE) tokenizer trained from scratch.

The rules that keep this honest
-------------------------------
* **Whitespace belongs to the token.** Pre-tokenization produces pieces like
  ``" world"`` (leading space included) and each piece ends with the ``</w>``
  marker, so ``decode(encode(text)) == text`` even for ``"a  b"`` or a trailing
  newline. A tokenizer that normalises whitespace is a tokenizer that lies.
* **Merges are applied in learned rank order** (lowest rank first). Applying them
  in any other order produces different tokens for the same text.
* **Training is deterministic.** Ties in pair frequency are broken by the pair
  itself, so two runs on the same corpus produce byte-identical merges.
* **Unknown characters are loud.** Without ``unknown_token`` they raise; with it,
  they collapse to that token and the information loss is explicit.

Example
-------
>>> corpus = ["the cat sat on the mat", "the cat sat on the mat"]
>>> tok = BPETokenizer.train(corpus, vocab_size=40, min_frequency=2)
>>> ids = tok.encode("the cat")
>>> tok.decode(ids)
'the cat'
>>> tok.tokenize("the cat")
['the</w>', ' cat</w>']
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

END_OF_WORD = "</w>"

# `\s*\w+` keeps leading whitespace inside the piece; `\s*[^\w\s]` does the same
# for punctuation. Anything left over (e.g. trailing spaces) is kept verbatim.
PIECE_PATTERN = re.compile(r"\s*\w+|\s*[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class Merge:
    """One learned merge rule: `left + right -> merged`, applied at `rank`."""

    left: str
    right: str
    merged: str
    rank: int

    @property
    def pair(self) -> tuple[str, str]:
        return self.left, self.right


def pretokenize(text: str) -> list[str]:
    """Split text into pieces that concatenate back to the original string."""
    pieces = PIECE_PATTERN.findall(text)
    joined = "".join(pieces)
    if joined != text:  # leftover whitespace (leading/trailing runs)
        remainder = text[len(joined) :]
        if remainder:
            pieces.append(remainder)
    return pieces


class BPETokenizer:
    """Vocabulary + merge table. Train once, then encode/decode/save/load."""

    def __init__(
        self,
        vocab: Mapping[str, int],
        merges: Sequence[Merge] = (),
        unknown_token: str | None = None,
    ) -> None:
        self._vocab = dict(vocab)
        self._merges = tuple(merges)
        self._id_to_token = {index: token for token, index in self._vocab.items()}
        if len(self._id_to_token) != len(self._vocab):
            raise ValueError("vocabulary ids must be unique")
        self._ranks = {merge.pair: merge.rank for merge in self._merges}
        self._merged = {merge.pair: merge.merged for merge in self._merges}

        self.unknown_token = unknown_token
        if unknown_token is not None and unknown_token not in self._vocab:
            raise ValueError(f"unknown_token {unknown_token!r} is not in the vocabulary")

    # ------------------------------------------------------------------ training
    @classmethod
    def train(
        cls,
        corpus: Iterable[str],
        vocab_size: int,
        *,
        unknown_token: str | None = None,
        special_tokens: Sequence[str] = (),
        min_frequency: int = 2,
    ) -> "BPETokenizer":
        """Learn merges from `corpus` until `vocab_size` tokens exist."""
        if vocab_size < 1:
            raise ValueError("vocab_size must be >= 1")
        if min_frequency < 1:
            raise ValueError("min_frequency must be >= 1")

        words: Counter[tuple[str, ...]] = Counter()
        for text in corpus:
            for piece in pretokenize(text):
                words[tuple(piece) + (END_OF_WORD,)] += 1
        if not words:
            raise ValueError("corpus is empty — nothing to train on")

        vocab: dict[str, int] = {}

        def add(token: str) -> bool:
            if token in vocab:
                return False
            vocab[token] = len(vocab)
            return True

        for special in special_tokens:
            add(special)
        if unknown_token is not None:
            add(unknown_token)
        for word in words:
            for symbol in word:
                add(symbol)

        if vocab_size < len(vocab):
            raise ValueError(
                f"vocab_size={vocab_size} is smaller than the {len(vocab)} initial characters"
            )

        merges: list[Merge] = []
        while len(vocab) < vocab_size:
            pairs: Counter[tuple[str, str]] = Counter()
            for word, frequency in words.items():
                for pair in zip(word, word[1:]):
                    pairs[pair] += frequency

            best: tuple[str, str] | None = None
            best_frequency = 0
            for pair, frequency in pairs.items():
                if frequency < min_frequency:
                    continue
                if pair[0] + pair[1] in vocab:  # collision: vocab would not grow
                    continue
                if (
                    best is None
                    or frequency > best_frequency
                    or (frequency == best_frequency and pair < best)
                ):
                    best, best_frequency = pair, frequency

            if best is None:
                break

            merged = best[0] + best[1]
            add(merged)
            merges.append(Merge(left=best[0], right=best[1], merged=merged, rank=len(merges)))
            words = Counter(
                {
                    tuple(_merge_sequence(word, best, merged)): frequency
                    for word, frequency in words.items()
                }
            )

        return cls(vocab, merges, unknown_token=unknown_token)

    # ------------------------------------------------------------------- encoding
    def encode(self, text: str) -> list[int]:
        """Text -> token ids, applying the lowest-ranked merge first."""
        ids: list[int] = []
        for piece in pretokenize(text):
            symbols = [self._resolve(symbol) for symbol in [*piece, END_OF_WORD]]
            self._apply_merges(symbols)
            ids.extend(self._vocab[symbol] for symbol in symbols)
        return ids

    def tokenize(self, text: str) -> list[str]:
        """The token strings behind `encode` — the thing you actually eyeball."""
        tokens: list[str] = []
        for piece in pretokenize(text):
            symbols = [self._resolve(symbol) for symbol in [*piece, END_OF_WORD]]
            self._apply_merges(symbols)
            tokens.extend(symbols)
        return tokens

    def decode(self, ids: Sequence[int]) -> str:
        """Token ids -> text. Raises on ids this vocabulary never produced."""
        pieces: list[str] = []
        for token_id in ids:
            try:
                pieces.append(self._id_to_token[token_id])
            except KeyError as exc:
                raise ValueError(f"unknown token id {token_id}") from exc
        return "".join(pieces).replace(END_OF_WORD, "")

    # ------------------------------------------------------------------ internals
    def _resolve(self, symbol: str) -> str:
        if symbol in self._vocab:
            return symbol
        if self.unknown_token is not None:
            return self.unknown_token
        raise ValueError(f"character {symbol!r} is not in the vocabulary")

    def _apply_merges(self, symbols: list[str]) -> None:
        while len(symbols) > 1:
            best_rank: int | None = None
            best_index = -1
            for index in range(len(symbols) - 1):
                rank = self._ranks.get((symbols[index], symbols[index + 1]))
                if rank is not None and (best_rank is None or rank < best_rank):
                    best_rank, best_index = rank, index
            if best_rank is None:
                return
            pair = (symbols[best_index], symbols[best_index + 1])
            symbols[best_index : best_index + 2] = [self._merged[pair]]

    # ------------------------------------------------------------------- metadata
    @property
    def vocab_size(self) -> int:
        return len(self._vocab)

    @property
    def merges(self) -> tuple[Merge, ...]:
        return self._merges

    @property
    def vocab(self) -> dict[str, int]:
        return dict(self._vocab)

    def compression_ratio(self, text: str) -> float:
        """Characters per token: higher is better (fewer tokens to pay for)."""
        tokens = len(self.encode(text))
        return len(text) / tokens if tokens else 0.0

    # ---------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "end_of_word": END_OF_WORD,
            "unknown_token": self.unknown_token,
            "vocab": [token for token, _ in sorted(self._vocab.items(), key=lambda kv: kv[1])],
            "merges": [
                {"left": m.left, "right": m.right, "merged": m.merged, "rank": m.rank}
                for m in self._merges
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "BPETokenizer":
        vocab = {token: index for index, token in enumerate(payload["vocab"])}
        merges = [
            Merge(
                left=item["left"],
                right=item["right"],
                merged=item["merged"],
                rank=int(item["rank"]),
            )
            for item in payload.get("merges", [])
        ]
        return cls(vocab, merges, unknown_token=payload.get("unknown_token"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "BPETokenizer":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"BPETokenizer(vocab_size={self.vocab_size}, merges={len(self._merges)})"


def _merge_sequence(
    word: Sequence[str], pair: tuple[str, str], merged: str
) -> list[str]:
    """Replace every non-overlapping occurrence of `pair` in `word` with `merged`."""
    result: list[str] = []
    index = 0
    while index < len(word):
        if (
            index + 1 < len(word)
            and word[index] == pair[0]
            and word[index + 1] == pair[1]
        ):
            result.append(merged)
            index += 2
        else:
            result.append(word[index])
            index += 1
    return result
