---
title: Lease-based lock with fencing tokens
category: backend
level: hard
concepts: leases, fencing tokens, clock injection, thread safety
status: solved
---

# Lease-based lock with fencing tokens

## Context

Two workers must not write the same invoice file at the same time. The obvious fix is
a lock, but a lock held with a *timeout* (a lease) has a hole that shows up in
production, not in tests: worker A grabs the lease, gets frozen by a long GC pause,
the lease expires, worker B takes over — and then A wakes up and writes anyway.
A's write lands on top of B's.

The fix is not a longer lease. It is a **fencing token**: every grant of the lease
comes with a number that only ever goes up, and the storage rejects any write whose
number is lower than the highest it has already applied. A is not stopped from
*writing*; it is stopped from *being applied*.

## Input / Output

```
input  -> acquire(owner) on a lease lock backed by an injectable clock
output -> FenceToken(owner, fence, expires_at) or LockHeld; renew/release return bool
         FenceGuard.write(token, value) -> applied fence, or StaleTokenError
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | A lease is live while `clock.now() < expires_at`; at or past it, the lock is free again | Expiry is the whole point of a lease |
| 2 | Every grant increments a fencing counter that never decreases, even across releases | The counter, not the lock, is what makes stale writes safe |
| 3 | `acquire` raises `LockHeld` when a live lease belongs to somebody else | Callers need to distinguish "busy" from "granted" |
| 4 | `renew` and `release` only succeed for the current token, and only while it is live | A stale owner must not be able to extend or drop someone else's lease |
| 5 | `FenceGuard.write` raises `StaleTokenError` when `token.fence <= highest seen` | This is the actual safety property |

## Constraints & acceptance criteria

- No wall-clock calls inside the lock: time comes from a `Clock` protocol, so tests are
  deterministic and a fake clock can jump forward instantly.
- Every state transition happens under one `threading.Lock`; a racing `acquire` must
  never hand out two live tokens.
- `FenceGuard` is the guarded resource: writes it accepts are applied in strictly
  increasing fence order.
- A stale holder's write must be rejected *after* a takeover, even though its token
  object still exists and looks valid to `is_expired`.

## Assumptions (write them down — real problems are under-specified)

- The guarded resource is a single in-process object. Durable fencing lives in the
  storage layer (a row version, an S3 conditional write, a Redis `WATCH`).
- Clocks are monotonic per process. Cross-machine clock skew is out of scope; that is
  why leases are renewed by the holder rather than coordinated.
- `renew` never shortens a lease.

## How to run

```bash
cd problems/backend/02-lease-based-lock-with-fencing-tokens
python -m unittest discover -s tests -t .
python -m solution.cli --help
python -m solution.cli --lease 10 --stall 15   # the stalled-writer story, end to end
```

## Stretch goals

- Persist the fence counter through a restart (a counter that resets is worse than none).
- Add `acquire(timeout=...)` that blocks until the lease frees, without busy-waiting.
- Emit an event every time `FenceGuard` rejects a stale write, and alert on it: it
  means some process is running far behind.
