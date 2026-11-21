---
title: Idempotency keys with a pluggable store
category: backend
level: hard
concepts: idempotency, replay, pluggable store, TTL, concurrency
status: solved
---

# Idempotency keys with a pluggable store

## Context

A mobile client sends `POST /charges` and the connection drops before the response
arrives. The client retries — as it must, because it never learned whether the charge
happened. Without help, the customer is charged twice.

The fix is an **idempotency key**: the client sends a key it chose, the server runs
the side effect once, stores the response, and replays that response for every later
request with the same key. The three failure modes that make this hard in practice:

1. **Same key, different body.** A client bug reuses `key-123` for a different amount.
   Replaying the old response would be a lie; executing would risk a second charge.
   The honest answer is a conflict, before anything runs.
2. **A crash mid-flight.** The key must not be *poisoned* by a failed attempt: if the
   handler raised, the operation did not happen, so a retry must be allowed to run it.
3. **Two requests at once.** The retry storm arrives while the first attempt is still
   running. Only one may execute.

## Input / Output

```
input  -> key (client-chosen string), payload (JSON-able), handler(payload) -> (status, body)
output -> Result(status, body, replayed): the same bytes the first caller got
errors -> IdempotencyConflict (same key, different payload), RequestInFlight
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | The fingerprint is a canonical hash of the payload (sorted keys), not the raw bytes | `{"a":1,"b":2}` and `{"b":2,"a":1}` are the same request |
| 2 | A replay returns the stored status **and** body, and does not call the handler again | The whole point: the side effect happens once |
| 3 | The same key with a different fingerprint raises `IdempotencyConflict` and runs nothing | Silence here is a double charge |
| 4 | A handler that raises stores nothing, so a retry may execute again | A crash must not wedge the key forever |
| 5 | A record expires after `ttl_ms`; an expired key behaves like a new one | Storage cannot grow forever, and clients eventually forget keys |
| 6 | Two calls with the same key at the same time: one executes, the other gets `RequestInFlight` or a replay | Retry storms are normal, not exceptional |
| 7 | The store is an interface: memory for tests, Redis/Postgres in production | The layer must not care where the bytes live |

## Constraints & acceptance criteria

- Standard library only; the clock is injected (`FakeClock` for tests), never
  `time.time()` inside the layer.
- Keys are validated: non-empty, no whitespace-only, at most 255 characters.
- `Result` and `Record` are immutable; a replayed `Result` is byte-identical to the
  original (same status, same body string).
- Expiry is checked on read, so a stale record can never be replayed.
- Statistics (`executed`, `replayed`, `conflicts`, `rejected`) are exposed for tests
  and metrics without reaching into private state.

## Assumptions (write them down — real problems are under-specified)

- The handler is safe to run once per payload; it is the caller's job to make the
  *side effect* atomic, this layer only prevents repeats.
- In-flight protection is per process. A multi-instance deployment needs a
  store-level reservation (a `SET NX` on the key); that is a documented stretch goal.
- TTL is measured from the successful execution, not from the first attempt.
- Bodies are stored as strings (JSON text); the layer never re-serialises them.

## How to run

```bash
cd problems/backend/03-idempotency-keys-with-a-pluggable-store
python -m unittest discover -s tests -t .
python -m solution.cli --key charge-1 --payload '{"amount": 10}' --calls 3
python -m solution.cli --key charge-2 --payload '{"amount": 10}' --fail-once --json
```

## Stretch goals

- A store-level reservation (`try_reserve`) so two processes cannot both execute.
- Store the request's `Idempotency-Key` header hash as well, to detect key reuse across
  endpoints.
- Emit a metric when an expired key is reused with a different payload (it is legal
  here, but it usually means a client is retrying a request from yesterday).
