---
title: Nginx access log report
category: data
level: medium
concepts: regex parsing, dataclasses, streaming aggregation, percentiles
status: solved
---

# Nginx access log report

## Context

You are on-call for a service. The only thing you have is `access.log` in nginx's
combined format, with `$request_time` appended — 40 000 lines of it. Before you
can fix anything you need answers: how much traffic, how much of it is failing,
which endpoints hurt, and when the traffic spikes.

Nobody will hand you a clean CSV. The file contains truncated lines from a killed
worker, health-probe spam, and one line of plain-text garbage where a log rotation
went wrong.

## Input

A text file, one request per line:

```
203.0.113.10 - - [20/Nov/2026:09:05:11 +0000] "GET /api/users HTTP/1.1" 200 512 "-" "curl/8.0" 0.023
```

Field order: `ip ident user [timestamp] "METHOD path protocol" status bytes "referer" "user-agent" request_time`.

## Output

A report with:

| Metric | Definition |
|--------|-----------|
| `total_requests` | number of lines parsed successfully |
| `malformed_lines` | non-blank lines that could not be parsed |
| `unique_clients` | distinct `ip` values |
| `error_rate` | share of requests with `status >= 400` (0.0 when empty) |
| `top_paths` | top N paths by hits, ties broken alphabetically |
| `p95_latency_ms` | 95th percentile of `request_time`, nearest-rank method |
| `busiest_hour` | `YYYY-MM-DD HH:00` bucket with the most requests, earliest on ties |

## Constraints & acceptance criteria

- Streaming friendly: must work when given a generator, never requires the whole
  file in memory.
- A bad line must never abort the report — it is counted and skipped.
- Blank / whitespace-only lines are **not** malformed; they are ignored.
- Deterministic output for identical input (stable tie-breaks, sorted results).
- `python -m solution.cli tests/fixtures/sample.log` prints the human-readable table,
  `--json` prints machine-readable output.

## Assumptions (written down because real systems need them)

- Timezone comes from the log's `[20/Nov/2026:09:05:11 +0000]` offset; the hour
  bucket therefore uses the log's own clock, not the server's.
- Percentile uses **nearest-rank**: `rank = ceil(0.95 * n)`, never interpolation.
- `request_time` is in seconds, reported in milliseconds.

## How to run

```bash
cd problems/data/01-nginx-log-report
python -m solution.cli tests/fixtures/sample.log
python -m solution.cli tests/fixtures/sample.log --json --top 3
python -m unittest discover -s tests -t .
```

## Stretch goals

- Parse the `+0000` offset and normalise every hour bucket to UTC.
- Add a `--since`/`--until` filter without loading the file twice.
- Emit Prometheus text format instead of JSON.
