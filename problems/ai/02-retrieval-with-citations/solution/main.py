"""A retrieval layer for a docs assistant: chunk, index, rank, cite.

Why it is built this way
------------------------
* **A citation must slice the source.** ``Chunk.start``/``end`` are offsets into the
  *original* text, so ``document[start:end] == chunk.text`` holds by construction and
  support can verify any answer with one line of Python.
* **Cuts prefer sentence boundaries.** A chunk that starts mid-word is retrieved
  badly and reads worse; so we look for ``. ! ?`` or a newline near the limit, then
  fall back to whitespace, then to a hard cut.
* **Ranking is TF-IDF cosine**, not keyword matching, and ties break on
  ``(doc_id, start)`` so answers are reproducible.
* **No signal means no answer.** A stopword-only query returns ``[]`` rather than the
  least-bad chunk — a retriever that always returns something invites hallucination.

Example
-------
>>> chunks = chunk_document("policy.md", "Alpha beta gamma. Delta epsilon.", size=20, overlap=5)
>>> [(c.start, c.end) for c in chunks]
[(0, 18), (13, 32)]
>>> "policy.md" in chunks[0].citation
True
>>> tokenize("The API key MUST rotate!")
['api', 'key', 'must', 'rotate']
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

WORD_PATTERN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """a an and are as at be been but by can do does for from has have how i if in into
    is it its of on or our so that the their then there these they this to was we were
    what when where which who why will with you your""".split()
)

SENTENCE_ENDINGS = (". ", "! ", "? ", ".\n", "!\n", "?\n", "\n\n")


@dataclass(frozen=True)
class Chunk:
    """A slice of a document, with a citation that can be verified."""

    doc_id: str
    start: int
    end: int
    text: str

    @property
    def citation(self) -> str:
        return f"{self.doc_id}#chars={self.start}-{self.end}"

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Hit:
    """A ranked chunk."""

    chunk: Chunk
    score: float

    @property
    def citation(self) -> str:
        return self.chunk.citation


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with the small built-in stopword list removed."""
    return [word for word in WORD_PATTERN.findall(text.lower()) if word not in STOPWORDS]


def chunk_document(
    doc_id: str,
    text: str,
    *,
    size: int = 200,
    overlap: int = 40,
) -> list[Chunk]:
    """Split `text` into overlapping, sentence-aware chunks with exact offsets."""
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    if overlap < 0:
        raise ValueError(f"overlap must be >= 0, got {overlap}")
    if overlap >= size:
        raise ValueError(f"overlap ({overlap}) must be smaller than size ({size})")
    if not text.strip():
        return []

    chunks: list[Chunk] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        if end < length:
            end = _best_cut(text, start, end, size)
        chunk_text = text[start:end]
        if chunk_text.strip():
            chunks.append(Chunk(doc_id=doc_id, start=start, end=end, text=chunk_text))
        if end >= length:
            break
        start = max(end - overlap, start + 1)  # always make progress
    return chunks


def _best_cut(text: str, start: int, hard_end: int, size: int) -> int:
    """Prefer a sentence end, then a space, in the second half of the window."""
    floor = start + max(size // 2, 1)
    window = text[floor:hard_end]

    best_end = -1
    for ending in SENTENCE_ENDINGS:
        index = window.rfind(ending)
        if index != -1:
            best_end = max(best_end, index + len(ending))
    if best_end > 0:
        return floor + best_end

    space = window.rfind(" ")
    if space > 0:
        return floor + space + 1
    return hard_end


class Retriever:
    """In-memory TF-IDF index over document chunks."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._term_frequencies: list[Counter[str]] = []
        self._document_frequency: Counter[str] = Counter()
        self._documents: set[str] = set()

    # ------------------------------------------------------------------ indexing
    def add_document(
        self, doc_id: str, text: str, *, size: int = 200, overlap: int = 40
    ) -> int:
        """Chunk and index one document. Returns the number of chunks added."""
        if not doc_id:
            raise ValueError("doc_id must be a non-empty string")
        if doc_id in self._documents:
            raise ValueError(f"document {doc_id!r} is already indexed")

        chunks = chunk_document(doc_id, text, size=size, overlap=overlap)
        self._documents.add(doc_id)
        for chunk in chunks:
            frequencies = Counter(tokenize(chunk.text))
            self._chunks.append(chunk)
            self._term_frequencies.append(frequencies)
            self._document_frequency.update(frequencies.keys())
        return len(chunks)

    def add_folder(
        self, folder: str | Path, *, suffixes: Sequence[str] = (".md", ".txt")
    ) -> int:
        """Index every matching file in a folder. Returns the number of chunks."""
        added = 0
        for path in sorted(Path(folder).iterdir()):
            if path.is_file() and path.suffix.lower() in suffixes:
                added += self.add_document(path.name, path.read_text(encoding="utf-8"))
        return added

    # ------------------------------------------------------------------ searching
    def search(self, query: str, *, k: int = 3, min_score: float = 0.0) -> list[Hit]:
        """Rank chunks against `query`; empty output when the query has no signal."""
        if k <= 0:
            return []
        terms = tokenize(query)
        if not terms:
            return []

        query_weights = self._weights(terms, Counter(terms))
        if not query_weights:
            return []

        hits: list[Hit] = []
        for chunk, frequencies in zip(self._chunks, self._term_frequencies):
            if not frequencies:
                continue
            score = _cosine(query_weights, self._weights(list(frequencies), frequencies))
            if score > min_score:
                hits.append(Hit(chunk=chunk, score=score))

        hits.sort(key=lambda hit: (-hit.score, hit.chunk.doc_id, hit.chunk.start))
        return hits[:k]

    def _weights(self, terms: Iterable[str], frequencies: Mapping[str, int]) -> dict[str, float]:
        """Sublinear TF × smoothed IDF, L2-normalised later inside `_cosine`."""
        total_documents = max(len(self._chunks), 1)
        weights: dict[str, float] = {}
        for term in set(terms):
            count = frequencies.get(term, 0)
            if not count:
                continue
            document_frequency = self._document_frequency.get(term, 0)
            idf = math.log((1 + total_documents) / (1 + document_frequency)) + 1.0
            weights[term] = (1.0 + math.log(count)) * idf
        return weights

    # ------------------------------------------------------------------- metadata
    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def document_ids(self) -> list[str]:
        return sorted(self._documents)

    def chunks_for(self, doc_id: str) -> list[Chunk]:
        return [chunk for chunk in self._chunks if chunk.doc_id == doc_id]


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    dot = sum(left[term] * right[term] for term in shared)
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


def render(query: str, hits: Sequence[Hit]) -> str:
    """Human-readable answer preview with citations."""
    if not hits:
        return f"query: {query!r}\nno passages matched — the assistant must say it does not know"
    lines = [f"query: {query!r}", f"{len(hits)} passage(s):"]
    for index, hit in enumerate(hits, start=1):
        text = " ".join(hit.chunk.text.split())
        if len(text) > 220:
            text = text[:217] + "…"
        lines += [f"  {index}. [{hit.score:.3f}] {hit.citation}", f"     {text}"]
    return "\n".join(lines)
