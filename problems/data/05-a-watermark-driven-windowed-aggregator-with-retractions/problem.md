---
title: A watermark-driven windowed aggregator with retractions
category: data
level: hard
concepts: event time, watermarks, sliding windows, late data, retractions, exactly-once
status: solved
---

# A watermark-driven windowed aggregator with retractions

## Context

The dashboard said 1,204 checkouts in the 12:00 minute. It was wrong, and nobody found
out for three weeks.

The pipeline aggregated by *arrival* time: a worker batched whatever had landed in its
queue in the last sixty seconds and wrote the count for "now". Mobile clients buffer
events while offline and flush hours later, so a phone that reconnected at 12:40 dumped
three hundred checkouts into the 12:40 bucket. Finance reconciled against the dashboard,
found a forty-minute spike with no orders behind it, and asked engineering to explain a
bug that had been silently rewriting history for months.

The fix is to aggregate by **event time** — the timestamp the phone recorded — instead of
by arrival time. That immediately raises the question every streaming system has to
answer: *when is a window done?* You cannot wait forever; you only have the data you
have. So you publish a **watermark**: a claim, derived from the data, that no event older
than `T` will be seen again. Windows whose end is at or below the watermark are emitted,
and everything still open stays in memory.

The hard part is what happens when the claim turns out to be false. A straggler shows up
after its window was already emitted. You cannot un-print a row a dashboard already read.
You can, however, emit a **retraction** — "subtract the row I gave you" — followed by the
corrected row. Downstream consumers that apply both end up exactly right; consumers that
ignore retractions are wrong in a way they can detect, which is the best a stream can do.

This problem asks you to build that aggregator: event-time windows (tumbling *and*
sliding), a monotonic watermark, bounded memory, deduplication of redelivered events, and
two honest policies for data that is too late.

## Input / Output

```
input  -> events: (key: str, ts: number, value: number = 1.0, id: str | None)
          window size, hop, allowed lateness, mode: "drop" | "retract"
output -> window records, in a deterministic order:
          (key, window_start, window_end, count, total, min, max, mean, retracted)
```

The CLI reads JSONL, one event per line, and writes one record per line:

```jsonl
{"key": "checkout", "ts": 1730000100, "value": 42.5, "id": "evt-1"}
```

```jsonl
{"count": 2, "key": "checkout", "max": 42.5, "mean": 21.75, "min": 1.0,
 "retracted": false, "total": 43.5, "window_end": 1730000160, "window_start": 1730000100}
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Windows are half-open `[start, start + size)` on **event time**, aligned to multiples of `hop`. | Mixing arrival time and event time is the bug in the context section; `[a, b)` is what makes adjacent windows partition the timeline instead of overlapping at every boundary. |
| 2 | `hop == size` is tumbling; `hop < size` is sliding, and one event joins **every** window containing it. | Sliding windows are how you get "last 5 minutes, refreshed every minute" — the same event is legitimately counted more than once. |
| 3 | `hop > size` is an error. | It leaves holes in the timeline: timestamps that belong to no window are silently dropped. Better to refuse than to lose data quietly. |
| 4 | `watermark = max(ts seen) - lateness`, started at `None`, **never decreasing**. | A watermark that moves backwards would re-open an emitted window and emit it twice. |
| 5 | A window is emitted exactly once, when `window_end <= watermark`, and then leaves the open set. | Bounded memory is the point: memory must be a function of open windows, not of the stream. |
| 6 | `flush()` emits everything still open, and a second `flush()` emits nothing. | End of stream is a watermark of `+∞`, but only once. |
| 7 | Emission order is `(window_end, window_start, key)` — deterministic across runs. | Two runs over the same input must diff to nothing, or nobody can review a backfill. |
| 8 | An event for an already-emitted window is **dropped and counted** in `drop` mode. | Silent loss is the thing you cannot debug at 3 a.m.; `stats.dropped_late` is the alarm. |
| 9 | In `retract` mode the same event emits a retraction (the old aggregate, `retracted=True`) followed by the corrected aggregate. | Downstream can subtract, then add. Two records, one truthful answer. |
| 10 | `net_totals(records)` applies retractions and returns what a correct consumer holds. | This is the acceptance test for the whole design: the net answer must not depend on arrival order. |
| 11 | Duplicate `id`s are counted once and reported in `stats.duplicates` (switchable off). | At-least-once delivery is the norm; the aggregator is where it becomes exactly-once numbers. |
| 12 | `count`, `sum`, `min`, `max`, `mean` per window; `mean` is `total / count`, never an average of averages. | Averages of averages is the second most common analytics bug in this list. |
| 13 | Stragglers that are late but still inside an **open** window are simply counted. | Lateness is not lateness until the window is closed; refusing them early throws away good data. |
| 14 | `stats` exposes `events_in`, `records_out`, `duplicates`, `dropped_late`, `windows_emitted`, `retractions`. | Every lossy decision is a number someone can alert on. |
| 15 | The CLI streams: one pass, no list of the whole input; exit `2` on unreadable input or a bad record, with the line or row number in the message. | "Which line?" is the only question you ask at 3 a.m. |

## Constraints & acceptance criteria

- Pure standard library, Python 3.10+; no dependency on wall-clock time or randomness.
- Memory: `windows_open` stays bounded while a stream of 100,000 events flows through
  windows of size 1,000 (the test suite asserts this, it is not a comment).
- Determinism: feeding the same events in a different arrival order must give the same
  `net_totals` whenever lateness is large enough to keep every window open; with retract
  mode it must hold for *any* arrival order, because retractions repair the difference.
- 88 unit tests and 14 doctests must pass, including the adversarial ones: watermark
  monotonicity, `hop > size`, double `flush()`, two stragglers in the same closed window,
  negative values, unicode keys, nanosecond timestamps, and malformed JSONL.

## Assumptions (write them down — real problems are under-specified)

- `ts` is a number in any unit the caller likes; the aggregator never interprets it as a
  wall-clock time. That is why `--size 60` works for seconds and `--size 60_000` for ms.
- A redelivered event is byte-identical, including its `id`. Two different events that
  happen to carry the same `id` are a producer bug this module cannot detect.
- `retract` mode keeps closed aggregates for the lifetime of the process so that a
  straggler can be corrected rather than lost. If that state must be bounded too, the
  retraction state belongs in an external store — see the stretch goals.
- Ordering is by window, then key. Within a key and window, events are commutative
  aggregates, so no finer order is needed.

## How to run

```bash
cd problems/data/05-a-watermark-driven-windowed-aggregator-with-retractions
python -m unittest discover -s tests -t .

printf '%s\n' \
  '{"key":"checkout","ts":1}' \
  '{"key":"checkout","ts":2}' \
  '{"key":"checkout","ts":11}' \
  '{"key":"checkout","ts":25}' \
  '{"key":"checkout","ts":5,"id":"straggler"}' \
| python -m solution.cli - --size 10 --stats
```

The last event arrives after `[0, 10)` was emitted, which is why the output contains a
`retracted: true` line:

```
{"count": 2, ..., "retracted": false, "window_end": 10, "window_start": 0}
{"count": 1, ..., "retracted": false, "window_end": 20, "window_start": 10}
{"count": 2, ..., "retracted": true,  "window_end": 10, "window_start": 0}
{"count": 3, ..., "retracted": false, "window_end": 10, "window_start": 0}
{"count": 1, ..., "retracted": false, "window_end": 30, "window_start": 20}
```

## Stretch goals

- **Session windows**: merge windows that are closer than a gap, which turns the fixed
  `(key, start)` map into an interval tree.
- **Bounded retraction state**: spill closed aggregates to disk keyed by window, and let a
  straggler read-modify-write, so `retract` mode does not grow without bound.
- **Allowed-lateness *per key***: some producers are known to be slow (batch uploaders),
  some are known to be prompt. One global lateness is a compromise nobody is happy with.
- **Watermark propagation**: expose the watermark as a metric and let the CLI `--emit-watermark`
  print it as it advances, which is what real engines do so downstream stages can close
  their own windows.
- **Two-phase emission**: emit early ("provisional") records and then a final one, instead
  of a single emit-then-maybe-retract. Cheaper for consumers that can tolerate a
  provisional value, but it makes "exactly once" a claim you have to prove per consumer.
