---
title: Byte-pair encoding tokenizer from scratch
category: ai
level: hard
concepts: tokenization, BPE, vocabularies, tie-breaking, lossless round-trip
status: solved
---

# Byte-pair encoding tokenizer from scratch

## Context

Every LLM pipeline starts with a tokenizer, and every tokenizer bug is a silent
one: text goes in, token ids come out, and a *different* text comes back. You cannot
debug a model whose inputs you cannot reproduce.

So: build the real thing — train a BPE vocabulary from a corpus, encode text into
ids, decode it back **losslessly**, and persist the vocabulary to disk so a model
checkpoint and its tokenizer can be versioned together.

## Input / Output

```python
tok = BPETokenizer.train(corpus_texts, vocab_size=400)   # learn merges
ids = tok.encode("Hello, world!")                        # -> [int, ...]
tok.decode(ids)                                          # -> "Hello, world!"  (exact)
tok.save("tokenizer.json")                               # ship it next to the weights
BPETokenizer.load("tokenizer.json").encode(text) == ids  # ... and reproduce it later
```

## Requirements

| # | Requirement | Why |
|---|-------------|-----|
| 1 | `decode(encode(text)) == text` for NL, punctuation, runs of spaces, newlines, Unicode | lossless is non-negotiable |
| 2 | Whitespace is part of a token, not a separator that gets normalised away | `"a  b"` must not become `"a b"` |
| 3 | Training is deterministic: same corpus + same `vocab_size` → identical merges | reproducible experiments |
| 4 | Merge ranks matter: the **lowest-ranked** applicable pair is merged first | this is what makes BPE decoding unambiguous |
| 5 | `vocab_size` is a hard cap, including special tokens | budgeting |
| 6 | Unknown characters raise — unless `unknown_token` was configured, in which case they degrade to that token and the loss is documented | silent data loss is worse than a crash |
| 7 | `save`/`load` round-trips the exact vocabulary and merge table | tokenizer must travel with the model |
| 8 | Ties in pair frequency break deterministically (lexicographic pair) | otherwise training is not reproducible across dict orderings |

## Constraints & acceptance criteria

- Words are pre-tokenized into `\s*\w+` / `\s*[^\w\s]` pieces, each terminated by the
  `</w>` end-of-word marker. That is what lets whitespace survive inside a token.
- `vocab_size` smaller than the initial character vocabulary → `ValueError`.
- A merge whose result already exists in the vocabulary is skipped (a collision would
  otherwise make the vocab stop growing and loop forever).
- `min_frequency` (default 2) stops training on pairs seen once — real BPE does this.
- Encode is O(n²) in piece length per merge scan; documented, fine for the exercises,
  and noted in the docstring as the place a production tokenizer would use a
  priority queue instead.

## How to run

```bash
cd problems/ai/01-bpe-tokenizer
python -m unittest discover -s tests -t .
python -m solution.cli --corpus tests/fixtures/corpus.txt --vocab-size 400 \
    --text "the tokenizer must round trip the same text"
```

## Stretch goals

- Byte-level fallback (GPT-2 style) so any UTF-8 input is encodable.
- Parallel merge counting to train on a 1 GB corpus.
- Compare compression against `tiktoken` on the same text.
