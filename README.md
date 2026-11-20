# problem-solving-daily

Real-world engineering problems, solved **end to end**: a written brief, runnable
code, a test suite, and a CLI you can actually run.

Most practice repositories are algorithm puzzles. This one is the messy stuff you
get paid for: a client's dirty CSV export, a rate limiter that must survive eight
threads, a folder of 900 files that has to be reorganised without losing one of
them. Every problem states its constraints and its assumptions — because that is
the part that is hard in real work.

**4 problems · 91 tests · 17 doctests · 3 categories · zero dependencies** (Python 3.10+)

---

## Catalog

| # | Problem | Category | Level | The real part |
|---|---------|----------|-------|---------------|
| 1 | [Nginx access log report](problems/data/01-nginx-log-report/problem.md) | `data` | 🟡 medium | truncated lines and probe spam must not break the report |
| 2 | [Clean a messy sales export](problems/data/02-messy-csv-sales-report/problem.md) | `data` | 🟡 medium | duplicates, 3 date formats, `(120.00)` refunds, exact money |
| 3 | [Per-key token bucket rate limiter](problems/backend/01-token-bucket-rate-limiter/problem.md) | `backend` | 🔴 hard | threads, injectable clock, LRU memory bound, `Retry-After` |
| 4 | [File organizer with undo](problems/cli/01-file-organizer/problem.md) | `cli` | 🟡 medium | dry-run, collision-safe renames, manifest-driven rollback |

Generated table with links, concepts and complexity of each: **[catalog.md](catalog.md)**
(regenerate with `python scripts/build_catalog.py`).

## Quick start

```bash
python scripts/run_tests.py             # every problem: tests + doctests
python scripts/run_tests.py --only data # just the data problems
python scripts/run_tests.py -v          # one line per test
```

Run one problem on its own:

```bash
cd problems/data/02-messy-csv-sales-report
python -m solution.cli tests/fixtures/sample_sales.csv
python -m unittest discover -s tests -t .
```

No `pip install` anywhere — standard library only, so a fresh clone works offline.

## Layout

```
.
├── problems/
│   ├── data/
│   │   ├── 01-nginx-log-report/
│   │   │   ├── problem.md              # brief: context, requirements, assumptions
│   │   │   ├── solution/
│   │   │   │   ├── main.py             # the logic (+ runnable >>> examples)
│   │   │   │   └── cli.py              # thin argparse wrapper
│   │   │   └── tests/
│   │   │       ├── test_main.py
│   │   │       └── fixtures/sample.log
│   │   └── 02-messy-csv-sales-report/…
│   ├── backend/01-token-bucket-rate-limiter/…
│   └── cli/01-file-organizer/…
├── scripts/
│   ├── run_tests.py                    # per-problem import isolation + doctests
│   ├── build_catalog.py                # problem.md front matter → catalog.md
│   └── new_problem.py                  # scaffold a new problem (red-first)
├── templates/                          # problem.md / module / test skeletons
├── catalog.md                          # generated
└── Makefile                            # make test | catalog | new | clean
```

## Add a problem

```bash
python scripts/new_problem.py data "Retry with exponential backoff" \
    --level medium --concepts "retries, backoff, jitter"

# or: make new CATEGORY=data TITLE='Retry with exponential backoff'
```

This creates `problem.md`, `solution/{__init__,main}.py`, `tests/{__init__,test_main}.py`
and a fixtures folder, then regenerates the catalog. The stub test **fails on
purpose**: you start from red, describe the behaviour, then make it green.

## Definition of done

1. **A brief somebody else could implement.** `problem.md` has context, input/output,
   numbered requirements with a *why*, and an explicit assumptions section.
2. **Solution code with doctests.** `solution/main.py` carries the reasoning in its
   docstring; runnable `>>>` examples are executed by the test runner.
3. **Tests that would catch the bug you actually fear.** Not just the happy example:
   empty input, malformed input, ties, collisions, clock going backwards, threads.
4. **A CLI when a human would run it.** `python -m solution.cli --help` must work.
5. **All green.** `python scripts/run_tests.py` passes, and `catalog.md` is regenerated.
6. **Trade-offs are written down.** Example: LRU eviction in the limiter weakens
   fairness; that is stated in the brief instead of hidden in the code.

## Design notes

- **`problem.md` is separate from `solution/`** so the brief reads like a ticket and
  the code stays code. The catalog is generated from the brief's front matter, so the
  docs cannot drift from the repository.
- **Front matter is parsed by hand**, not with PyYAML — one more dependency for five
  keys is not worth it (see `scripts/build_catalog.py`).
- **Each problem owns a `solution` package**, which is why the runner builds a clean
  import context per problem instead of one global `unittest discover`.
- **Time is injected, not slept on.** The limiter takes a `clock` callable, so the
  concurrency tests are deterministic and finish in milliseconds.
- **Money is `Decimal`.** `0.1 + 0.2 != 0.3` is a real bug report in finance code.

## Roadmap (planned problems)

- `data/03` — streaming dedupe of a 10 GB JSONL file with constant memory
- `backend/02` — idempotency keys with a pluggable store
- `backend/03` — cursor pagination over a mutable list
- `cli/02` — safe bulk-rename with a regex preview and `--dry-run`
- `data/04` — timezone-correct hourly aggregation across DST boundaries

## License

[MIT](LICENSE) © sayanoxprivatelimited
