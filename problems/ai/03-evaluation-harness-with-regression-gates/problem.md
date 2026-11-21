---
title: Evaluation harness with regression gates
category: ai
level: hard
concepts: precision, recall, cost per 1k, regression gates, thresholds
status: solved
---

# Evaluation harness with regression gates

## Context

You ship a classifier that tags support tickets. Every change — a prompt tweak, a new
model, a reranker — is judged by an "eval": run 400 labelled tickets, compute
precision and recall, compare with yesterday, and decide whether to deploy.

The dangerous version of this tool prints two numbers and lets a human squint at
them. Two days later recall drops from 0.82 to 0.79, nobody notices, and the
regression ships. The useful version fails a build: it knows the floors, it knows
what "regression" means in numbers, it charges every run for its tokens/requests,
and it exits non-zero with a list of reasons.

## Input / Output

```
input  -> Example(case_id, expected_labels, predicted_labels, cost_usd) per ticket
output -> Score: micro precision / recall / F1, exact-match accuracy, cost per 1,000
          Verdict: passed or a list of human-readable failures
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Labels are **sets**; precision and recall are micro-averaged over all cases | A ticket can carry two labels; counting per-case accuracy hides both misses |
| 2 | `accuracy` is exact match (predicted set == expected set) | Micro F1 flatters a model that gets one of two labels right on every case |
| 3 | A case with no predictions and no expectations counts as correct, not as a divide-by-zero | Empty responses are a real outcome and must not crash the harness |
| 4 | Cost is reported per **1,000 cases**, not per run | A budget is written per request; per-run cost changes meaning with suite size |
| 5 | The gate supports absolute floors (precision, recall, cost ceiling) **and** relative regressions against a baseline | "Better than yesterday" and "good enough to ship" are different questions |
| 6 | Every failure is reported, not just the first, in a stable order | A build log should tell you everything that is wrong in one run |
| 7 | The CLI exits 0 on pass, 1 on gate failure, 2 on bad input | CI needs to tell a broken suite from a failing model |

## Constraints & acceptance criteria

- Standard library only; no numpy, no sklearn.
- `Score` and `Verdict` are immutable (`frozen=True`) and every failure string is
  deterministic — the same two scores always produce the same list.
- An empty suite and duplicate case ids are errors, not silent zeroes: a harness that
  scores nothing must never pass a gate.
- A bare string label (`"ok"`) means one label, not five characters.
- Floats are only compared through rounded formatting in messages (`0.667`), so a
  failure log is stable across machines.

## Assumptions (write them down — real problems are under-specified)

- Costs are already measured per case by the caller (tokens × price, or request
  count); the harness only sums and divides.
- Labels are compared as exact strings: `ok` and `OK` are different labels.
- The baseline is a previous `Score` from the *same* suite definition; comparing
  across suites is the caller's mistake and is not detected.
- `max_drop` and `max_cost_increase` are non-negative tolerances; `0.0` means "no
  regression allowed at all".

## How to run

```bash
cd problems/ai/03-evaluation-harness-with-regression-gates
python -m unittest discover -s tests -t .
python -m solution.cli tests/fixtures/baseline.json tests/fixtures/candidate.json --min-precision 0.8
python -m solution.cli baseline.json candidate.json --max-drop 0.01 --json
```

## Stretch goals

- Per-label precision/recall (`score.by_label`) and a gate that targets one label.
- Bootstrap confidence intervals, so a 0.005 drop on 40 cases does not fail the build.
- A JSON baseline file with a schema version, so an old baseline cannot be read by
  accident.
