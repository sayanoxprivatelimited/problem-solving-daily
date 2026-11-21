---
title: Streaming dedupe of a huge JSONL file
category: data
level: medium
concepts: streaming, external memory, hashing, sharding, bounded memory
status: solved
---

# Streaming dedupe of a huge JSONL file

## Context

An analytics dump holds 40 million JSONL records, and 12% of them are the same event
delivered twice. The obvious fix —

```python
seen = set()
for line in open("dump.jsonl"):
    event = json.loads(line)
    if event["id"] not in seen:
        seen.add(event["id"])
        print(line, end="")
```

— needs roughly 40 million Python strings plus a set: several gigabytes, on a machine
that has 512 MB for the job. The script does not fail with a helpful error; it gets
killed by the OOM reaper halfway through, and you are left with a truncated output.

The technique that actually works is **external memory**: never hold every key at
once. Shard the stream by a hash of the key, then solve one small shard at a time,
then stitch the survivors back into the original order. Memory is bounded by the
shard size, not by the input; disk holds the rest.

## Input / Output

```
input  -> an iterable of JSON objects (from a file, stdin, or a generator)
output -> the same objects, first occurrence only, in the original order
config -> key(record), shards, max_keys_in_memory, tmp_dir
errors -> DedupeError (missing key, unreadable line, memory budget exhausted)
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Order is preserved: the first occurrence of every key survives, in input order | A deduped export that reorders rows breaks every downstream join |
| 2 | Memory is bounded by `max_keys_in_memory`, not by the input size | The whole point; the naive set dies here |
| 3 | A shard that still does not fit is re-sharded recursively, not loaded anyway | Real inputs have hot keys; one bucket can hold half the data |
| 4 | Duplicates are detected across shards — the hash decides placement, never the value | A duplicate in another shard must still be a duplicate |
| 5 | Temp files are cleaned up on success **and** on failure | A crash must not fill `/tmp` |
| 6 | Stats report peak keys, shards opened and re-shards, so the budget is provable | "Bounded memory" needs a number, not a promise |

## Constraints & acceptance criteria

- Standard library only; one pass over the source is read, the rest is over spooled
  shard files in a temporary directory.
- Keys may be any JSON-serialisable value (strings, numbers, lists); a caller-supplied
  function turns a record into its key.
- Duplicate detection compares a 64-bit BLAKE2b digest, so a false duplicate is a
  2⁻⁶⁴ event per pair — documented, and cheap enough to be worth it.
- `dedupe_in_memory` exists as the baseline the tests compare against: identical
  survivors in identical order, for random data.
- A record without a usable key raises `DedupeError` naming the line number.

## Assumptions (write them down — real problems are under-specified)

- JSONL means one JSON object per line; blank lines are skipped.
- The input is read exactly once, so it may be a generator or stdin.
- Spool files live in `tempfile.mkdtemp()` under `tmp_dir`; disk must have room for
  roughly the input size plus the survivors.
- `max_keys_in_memory` counts *distinct keys per bucket*, not lines: the spool step
  is O(1) in memory because it streams.
- Hashing has a collision floor: a small key space split into many buckets can still
  leave three keys together. The budget is a memory bound, not a guarantee, so pick
  `max_keys` with a margin (one bucket above the cap triggers a split, and the split
  is cheap).

## How to run

```bash
cd problems/data/04-streaming-dedupe-of-a-huge-jsonl-file
python -m unittest discover -s tests -t .
python -m solution.cli tests/fixtures/events.jsonl --key id --stats
python -m solution.cli tests/fixtures/events.jsonl --key id --max-keys 2 --stats --json
```

## Stretch goals

- Sort-merge dedupe (no hashing at all) when the key is already ordered in the file.
- A Bloom filter pre-pass, to skip spooling records that are certainly new.
- Resume after a crash by recording which shards were completed.
