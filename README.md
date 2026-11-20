# problem-solving-daily

Daily problem-solving practice with solutions, explanations, and progress tracking.

One folder per day, one file per problem. Every solution carries its own problem
statement, reasoning and complexity analysis in the docstring, ships with tests
that cross-check it against a brute force, and lands in a progress table that is
easy to scan a year later.

**Language:** Python 3.10+ · **Focus:** DSA / interview patterns · **Dependencies:** none

---

## Quick start

```bash
python scripts/run_tests.py          # unit tests + every docstring example
python scripts/run_tests.py -v       # per-test output
python scripts/run_tests.py --doctests
```

No `pip install` needed — the tests use the standard library only.

## Layout

```
.
├── days/
│   └── 2026-11-20/                   # one folder per day of practice
│       ├── README.md                 # what was learned, what was hard
│       ├── two_sum.py                # problem + approach + complexity + doctests
│       ├── test_two_sum.py           # unittest + random brute-force cross-check
│       ├── longest_substring.py
│       ├── test_longest_substring.py
│       ├── coin_change.py
│       └── test_coin_change.py
├── scripts/
│   ├── run_tests.py                  # zero-dependency test runner
│   └── new_problem.py                # scaffold a new problem + tracker row
├── templates/
│   ├── solution_template.py          # problem / approach / complexity skeleton
│   └── day_readme_template.md
├── progress.md                       # the table: solved, topic, complexity, status
└── README.md
```

## Progress

| Difficulty | Solved |
|-----------|--------|
| 🟢 Easy | 1 |
| 🟡 Medium | 2 |
| 🔴 Hard | 0 |
| **Total** | **3** |

Full table with links and complexities: **[progress.md](progress.md)**
Latest session: **[2026-11-20](days/2026-11-20/README.md)** — Two Sum ·
Longest Substring Without Repeating Characters · Coin Change.

## Add a problem

```bash
python scripts/new_problem.py "Trapping Rain Water" --topic "Two Pointers"
python scripts/new_problem.py "Word Ladder" --difficulty Hard --date 2026-11-21
python scripts/new_problem.py "Two Sum" --dry-run    # preview, touch nothing
```

That creates the solution stub, the test stub and (for a new day) the day README,
then appends a ⏳ row to `progress.md`. Nothing is ever overwritten.

## Conventions

1. **One problem = one file**, named after the problem in `snake_case`.
2. The **docstring is the write-up**: statement, approach, complexity, edge cases.
   Runnable `>>>` examples are mandatory — they are executed by the test runner.
3. **Every solution has tests**, including at least one brute-force cross-check on
   seeded random input where that is feasible. Fix the seed so runs are stable.
4. **Try before you look.** The file is not committed until the tests are green.
5. Mark a problem 🟨 if you solved it but would not reproduce it unaided, and add
   it to the revision queue in `progress.md`.

## Why the custom test runner?

Day folders are named `days/2026-11-20`, and a dash is not a valid character in a
Python module name, so `python -m unittest discover` cannot import them. The
runner loads each test file by path under a unique module name instead. Running a
single file directly also works:

```bash
python days/2026-11-20/test_coin_change.py
```

## License

[MIT](LICENSE) © sayanoxprivatelimited
