---
title: Sessionize an out-of-order event stream
category: data
level: hard
concepts: event time, watermarks, session windows, late data
status: solved
---

# Sessionize an out-of-order event stream

## Context

Product wants "sessions": a user's activity grouped so that two events belong
together while they are at most `gap` apart, and a new session starts after a longer
silence. The events arrive from mobile clients, which means they arrive **late and
out of order** — an event stamped 10:00:03 can show up after one stamped 10:00:09,
sometimes minutes later, sometimes after a retry.

Doing this in arrival order produces sessions that change retroactively, which is
exactly what a dashboard must never do. Doing it after collecting everything is not
possible on a stream. The standard answer is a **watermark**: a "nothing older than
this can still arrive" line, used to close sessions early and to drop events that
turn up too late.

## Input / Output

```
input  -> Event(ts, key, value) added in arrival order, gap_ms and allowed_lateness_ms
output -> Session(key, start, end, events) emitted in a deterministic order,
          plus stats: dropped-late count and how many sessions are still open
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Two events of the same key join a session while `next.ts - last.ts <= gap` | The gap rule is the definition of a session; it must be exact at the boundary |
| 2 | An event that arrives late and bridges two open sessions merges them into one | Out-of-order input must not produce two half-sessions |
| 3 | A session closes once `end + gap <= watermark`, where `watermark = max_ts_seen - allowed_lateness` | That is the only point after which the session provably cannot grow |
| 4 | An event with `ts < watermark` is dropped and counted, never merged | Keeping impossible events would make the output non-deterministic |
| 5 | `flush()` emits everything left over, sorted by `(key, start)` | End of stream must produce the same sessions a batch job would |

## Constraints & acceptance criteria

- One pass over the input: memory is bounded by the number of *open* sessions, not by
  the number of events seen.
- Emitted sessions are immutable (`frozen=True`) and their `events` tuple is sorted by
  `(ts, arrival)` so the output does not depend on thread scheduling.
- Every close is final: once emitted, a session never changes — even if a late event
  arrives that "should" have extended it (it is either merged before close, or dropped).
- No wall-clock use anywhere: `ts` values come from the events.

## Assumptions (write them down — real problems are under-specified)

- `ts` is epoch milliseconds and non-negative; equal timestamps are allowed.
- `allowed_lateness` is given in milliseconds and is the same for every key.
- A session belongs to exactly one key; keys never mix.
- Arrival order is only used as a tie-breaker, never for grouping.

## How to run

```bash
cd problems/data/03-sessionize-an-out-of-order-event-stream
python -m unittest discover -s tests -t .
python -m solution.cli --gap 5000 --lateness 2000 events.jsonl
printf '%s\n' '{"ts": 1000, "key": "u1"}' | python -m solution.cli --gap 5000 -
```

## Stretch goals

- Add per-key gaps (a browsing session and a checkout session are not the same length).
- Emit `late` events to a dead-letter list instead of dropping them.
- Bound the open-session map with an LRU and document what that trades away.
