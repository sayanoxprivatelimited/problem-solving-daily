---
title: Per-key token bucket rate limiter
category: backend
level: hard
concepts: concurrency, locks, monotonic clocks, LRU eviction, testable time
status: solved
---

# Per-key token bucket rate limiter

## Context

Your API is being hammered — sometimes by a buggy client in a retry loop, sometimes
by a scraper. You need an in-process limiter: a client may burst up to `capacity`
requests instantly, then gets `refill_rate` requests per second back, and **every
client is limited independently**.

This is the classic answer to "how do I stop one tenant from eating the service?".
The part that separates a toy implementation from a real one is not the refill
maths — it is testability, memory, and safety under threads.

## Input / Output

```python
limiter = RateLimiter(capacity=10, refill_rate=2, clock=...)   # clock is injectable
limiter.check("tenant-a")        # -> Decision(allowed=True, remaining=9.0, retry_after=0.0)
limiter.check("tenant-a", cost=5)  # weighted: a heavy endpoint costs more
```

`Decision` must tell the caller what to do next: `allowed`, tokens `remaining`, and
`retry_after` seconds (so the API can emit a correct `Retry-After` header).

## Requirements

| # | Requirement | Why |
|---|-------------|-----|
| 1 | Burst of `capacity`, then refill at `refill_rate` per second | token bucket semantics |
| 2 | `retry_after` = time until **this** request would fit | correct 429 headers |
| 3 | Refill never exceeds `capacity` | idle clients must not bank credit forever |
| 4 | Injectable clock, no `time.sleep` in tests | deterministic tests, no flakiness |
| 5 | Thread-safe: 8 threads × 50 requests on a 10-token bucket → exactly 10 allowed | real servers are concurrent |
| 6 | Bounded memory: `max_keys` with LRU eviction | one hostile client must not OOM the process |
| 7 | Weighted `cost` | `/export` is not `/health` |
| 8 | Rejected requests do not consume tokens | a denied request must not make the wait longer |

## Constraints & acceptance criteria

- `cost > capacity` can never succeed; `retry_after` is `inf` when no refill happens.
- `capacity >= 0`, `refill_rate >= 0`, `cost > 0`, non-empty keys; anything else raises
  `ValueError` instead of failing silently.
- Denied requests leave `remaining` untouched.
- `python -m solution.cli --capacity 5 --rate 1 --requests 10` prints a timeline you
  can eyeball.

## Documented trade-off (the interview-worthy bit)

LRU eviction means an evicted key comes back with a **full** bucket — a client can
"reset" its budget by staying quiet until it is evicted. That is the right trade-off
for an in-process guard (cheap, no coordination) and the wrong one for billing-grade
quotas, where the bucket belongs in Redis with a TTL and an atomic `EVAL`.

Also documented: a single lock is fine up to roughly 100k keys / 10k rps. Beyond
that, shard the dict by `hash(key) % N` and take one lock per shard.

## How to run

```bash
cd problems/backend/01-token-bucket-rate-limiter
python -m solution.cli --capacity 5 --rate 1 --requests 10
python -m unittest discover -s tests -t .
```

## Stretch goals

- Sliding-window-log limiter and a comparison of burst behaviour.
- Metrics hooks: `on_denied(key)` for Prometheus counters.
- Redis backend with a Lua script for a shared budget across pods.
