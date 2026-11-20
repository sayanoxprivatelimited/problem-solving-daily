---
title: Retrieval step for a docs assistant
category: ai
level: medium
concepts: chunking, TF-IDF, cosine similarity, citations, determinism
status: solved
---

# Retrieval step for a docs assistant

## Context

A support assistant answers from your documentation. Before any model is involved,
something has to **find the right paragraphs** — and every answer must cite the exact
place it came from, or support cannot verify it.

So build the retrieval layer: chunk documents with overlap, index them, rank them
against a query, and return citations with character ranges that point back into the
original file. No vector database, no API key, no dependencies.

## Input / Output

```python
retriever = Retriever()
retriever.add_document("security.md", text)          # -> number of chunks
hits = retriever.search("how do I rotate an api key", k=3)

hits[0].chunk.citation      # 'security.md#chars=0-198'
hits[0].chunk.text          # the passage itself
docs["security.md"][hits[0].chunk.start:hits[0].chunk.end] == hits[0].chunk.text
```

## Requirements

| # | Requirement | Why |
|---|-------------|-----|
| 1 | Chunks never exceed `size` characters and overlap by `overlap` characters | context + no answer cut in half |
| 2 | Cuts prefer sentence boundaries, then whitespace, then a hard cut | a chunk that starts mid-word retrieves badly and reads worse |
| 3 | `chunk.citation` + `start`/`end` must slice the **original** text exactly | verifiable citations are the whole point |
| 4 | Empty/whitespace text → no chunks; `0 <= overlap < size` enforced | bad configuration must fail loudly |
| 5 | Ranking is TF-IDF cosine over the index, not a keyword `if` | "rotate" must match "rotation" less than "rotate the key" |
| 6 | Deterministic ordering: score desc, then `(doc_id, start)` | reproducible answers, testable output |
| 7 | Stopword-only or unknown-term queries return `[]` | no hallucinated passage for a query with no signal |
| 8 | Registering the same `doc_id` twice raises | silent index corruption is worse than a crash |

## Constraints & acceptance criteria

- Overlap is measured in characters and never re-emits whitespace-only chunks.
- Progress is guaranteed: `start` always advances, even for pathological text.
- `k` and `min_score` filter after ranking; `k <= 0` returns `[]`.
- Citations look like `doc_id#chars=start-end`, 0-based, end exclusive — the same
  convention Python slicing uses, so verification is one line of code.

## Documented limits (a real system needs these written down)

- TF-IDF is unigram-only: no synonyms, no stemming. Good enough for a docs corpus of
  a few hundred chunks; swap in BM25 or an embedding index behind the same interface
  when recall matters.
- Chunking is character-based. A token-aware splitter (respecting code blocks and
  tables) is the natural next step and is listed as a stretch goal.
- `search` is O(chunks × query terms). For 10k+ chunks, precompute sparse vectors.

## How to run

```bash
cd problems/ai/02-retrieval-with-citations
python -m unittest discover -s tests -t .
python -m solution.cli tests/fixtures/docs --query "how do I rotate an api key" --k 3
```

## Stretch goals

- BM25 scoring and a recall@k comparison against the TF-IDF baseline.
- Chunk on markdown headings so a citation can name a section.
- HyDE-style query expansion using a cheap local model.
