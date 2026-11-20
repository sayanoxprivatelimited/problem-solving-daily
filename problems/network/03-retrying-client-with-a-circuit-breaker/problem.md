---
title: Retrying client with a circuit breaker
category: network
level: hard
concepts: retries, jitter, circuit breaker, idempotency
status: solved
---

# Retrying client with a circuit breaker

## Context

Your service calls a partner API. It is down for four minutes every other Tuesday, and
every one of your 200 workers retries the failing call three times — so the partner
sees 600 requests per second of pure noise while it is trying to come back, and your
threads are all parked in `sleep()`.

Two mechanisms are needed, and they are not the same thing:

- **Retries** handle a blip. They need backoff, they need jitter (otherwise every
  worker retries in lockstep), and they must know whether retrying is *safe*: a
  `POST /payments` that timed out after the request was sent may already have been
  charged.
- **A circuit breaker** handles an outage. After enough consecutive failures, stop
  calling for a while, so the failed dependency can recover and your workers stop
  burning time on it. Then let exactly one request through to find out if it is back.

## Input / Output

```
input  -> a callable, a RetryPolicy, a CircuitBreaker backed by an injectable clock
output -> the callable's value, or CircuitOpen / RetriesExhausted; a stats dict
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Delay before retry *n* is `base * multiplier ** (n-1)`, capped at `max_delay`, multiplied by jitter | Exponential backoff without jitter is a thundering herd with extra steps |
| 2 | A non-idempotent call is only retried when the request never reached the server | Retrying a sent `POST` can double-charge a customer |
| 3 | The breaker opens after `failure_threshold` consecutive failures and then rejects calls without touching the network | That is the whole point: fail fast while the dependency is broken |
| 4 | Once `reset_timeout` has elapsed, exactly one trial call is allowed; its outcome closes or re-opens the breaker | One probe, not a stampede |
| 5 | Every failure of every attempt counts toward the breaker, and a success resets the count | Half the protection lives in the bookkeeping |

## Constraints & acceptance criteria

- Time is injected (`Clock`) and sleeping is injected (`Sleeper`), so tests never
  sleep and every scenario is deterministic.
- `RetriesExhausted` carries the last error and the number of attempts actually made.
- Permanent errors (`PermanentError`) are never retried, whatever the policy says.
- `stats` reports: calls, attempts, retries, total slept, and calls short-circuited by
  an open breaker.

## Assumptions (write them down — real problems are under-specified)

- Failures counted by the breaker are consecutive, per client instance, not global.
- `RetryPolicy.attempts` includes the first try (4 means up to 4 requests).
- Jitter is injected as a zero-argument callable returning a float in `[0, 1]`; the
  default returns `1.0` so behaviour is reproducible.
- A half-open trial is one call; concurrent callers wait, they do not queue up.

## How to run

```bash
cd problems/network/03-retrying-client-with-a-circuit-breaker
python -m unittest discover -s tests -t .
python -m solution.cli --failures 5 --threshold 2 --reset 5
```

## Stretch goals

- Add a token-bucket retry budget (retries may not exceed 10% of traffic).
- Record outcomes per endpoint and open the breaker per host, not per client.
- Feed `stats` into Prometheus: `retries_total`, `breaker_open_total`.
