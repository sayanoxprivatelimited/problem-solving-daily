# Sayanox Problem Solve Daily

**Sayanox Problem Solve Daily** is the public practice log of
[sayanoxprivatelimited](https://github.com/sayanoxprivatelimited): real-world
engineering problems, solved **end to end** — a written brief, runnable code, a test
suite, and a CLI you can actually run.

Most practice repositories are algorithm puzzles. This one is the messy stuff you
get paid for: a client's dirty CSV export, a tokenizer that must not lose a
whitespace, an HTTP parser that must survive hostile chunking, a rate limiter that
must hold under eight threads. Every problem states its constraints and its
assumptions — because that is the part that is hard in real work.

**18 problems · 568 tests · 184 doctests · 6 categories · zero dependencies** (Python 3.10+)

Live site: **<https://problem-solving-daily.vercel.app>** — a **3D showcase** (four
WebGL scenes, search, detail sheet, light/dark theme, shareable deep links) built from
the same data, with no build step. Run it locally with `make serve` →
<http://localhost:8000>.

---

## Catalog

| # | Problem | Category | Level | The real part |
|---|---------|----------|-------|---------------|
| 1 | [Byte-pair encoding tokenizer](problems/ai/01-bpe-tokenizer/problem.md) | `ai` | 🔴 hard | lossless round-trip, merge ranks, deterministic training, save/load |
| 2 | [Retrieval step for a docs assistant](problems/ai/02-retrieval-with-citations/problem.md) | `ai` | 🟡 medium | sentence-aware chunking, TF-IDF ranking, citations that slice the source |
| 3 | [Per-key token bucket rate limiter](problems/backend/01-token-bucket-rate-limiter/problem.md) | `backend` | 🔴 hard | threads, injectable clock, LRU memory bound, `Retry-After` |
| 4 | [Lease-based lock with fencing tokens](problems/backend/02-lease-based-lock-with-fencing-tokens/problem.md) | `backend` | 🔴 hard | a stalled holder cannot write after its lease expired; fencing tokens at the store |
| 5 | [File organizer with undo](problems/cli/01-file-organizer/problem.md) | `cli` | 🟡 medium | dry-run, collision-safe renames, manifest rollback, path-traversal guard |
| 6 | [Build order from package manifests](problems/coding/01-build-order/problem.md) | `coding` | 🟡 medium | cycle reported as a path, alphabetical determinism, rebuild closure |
| 7 | [Nginx access log report](problems/data/01-nginx-log-report/problem.md) | `data` | 🟡 medium | truncated lines must not break the report, nearest-rank p95 |
| 8 | [Clean a messy sales export](problems/data/02-messy-csv-sales-report/problem.md) | `data` | 🟡 medium | duplicates, 3 date formats, `(120.00)` refunds, exact money |
| 9 | [Sessionize an out-of-order event stream](problems/data/03-sessionize-an-out-of-order-event-stream/problem.md) | `data` | 🔴 hard | event time vs arrival time, watermarks, session windows, counted late drops |
| 10 | [Incremental HTTP/1.1 parser](problems/network/01-incremental-http-parser/problem.md) | `network` | 🔴 hard | arbitrary TCP chunking, chunked encoding, smuggling defenses |
| 11 | [CIDR planner for a VPC](problems/network/02-cidr-planner/problem.md) | `network` | 🟡 medium | subnet maths, overlap detection, prefix aggregation, stdlib parity |
| 12 | [Retrying client with a circuit breaker](problems/network/03-retrying-client-with-a-circuit-breaker/problem.md) | `network` | 🔴 hard | exponential backoff with jitter, half-open probe, never retry a sent request |
| 13 | [Evaluation harness with regression gates](problems/ai/03-evaluation-harness-with-regression-gates/problem.md) | `ai` | 🔴 hard | precision/recall/cost per 1k, floors and allowed regression, a verdict you can gate CI on |
| 14 | [Idempotency keys with a pluggable store](problems/backend/03-idempotency-keys-with-a-pluggable-store/problem.md) | `backend` | 🔴 hard | same key + different body is a conflict, a crash does not poison the key, TTL, in-flight guard |
| 15 | [Cursor pagination over a mutable list](problems/backend/04-cursor-pagination-over-a-mutable-list/problem.md) | `backend` | 🟡 medium | keyset + snapshot cursors, signed and versioned, no skips when rows are inserted mid-walk |
| 16 | [Unified diff and patch engine](problems/coding/02-unified-diff-and-patch-engine/problem.md) | `coding` | 🔴 hard | Myers O(ND), hunk merging, no-newline markers, drift-tolerant application that refuses a partial patch |
| 17 | [Streaming dedupe of a huge JSONL file](problems/data/04-streaming-dedupe-of-a-huge-jsonl-file/problem.md) | `data` | 🟡 medium | bounded memory, hash sharding with recursive re-shard, input order preserved |
| 18 | [WebSocket frame decoder](problems/network/04-websocket-frame-decoder/problem.md) | `network` | 🔴 hard | arbitrary chunking, masking, fragmentation, control frames, close codes 1002/1007/1009 |

Generated table with links, concepts and per-category stats: **[catalog.md](catalog.md)**
(regenerate with `python scripts/build_catalog.py`).

## Findability (SEO & sharing)

The showcase is meant to be found and forwarded, so the crawl-facing parts are
generated instead of hand-maintained:

| What | Where | Kept honest by |
|---|---|---|
| title, description, keywords, robots, canonical | `site/index.html` `<head>` | `site/tests/seo.test.mjs` |
| Google Search Console ownership token | `site/index.html` `<head>` | asserted verbatim by the same test |
| Open Graph + Twitter card, 1200×630 preview | `site/assets/og-cover.jpg` | dimension check in the same test |
| schema.org graph (WebSite, Organization, CollectionPage, ItemList of 18 `LearningResource`s) | generated block in `site/index.html` | `python scripts/build_seo.py --check` |
| crawl policy + sitemap pointer | `site/robots.txt` | generated, checked |
| canonical URL | `site/sitemap.xml` | generated, checked |
| deep links (`?q=`, `?p=<slug>`, `#problem-<slug>`) | `site/js/main.js` | wiring tests + jsdom smoke |
| share targets (X, LinkedIn, WhatsApp, Telegram, Reddit, copy-link, native share sheet) | footer + detail sheet | share test |

```bash
python scripts/build_seo.py          # rewrite robots.txt, sitemap.xml, JSON-LD
python scripts/build_seo.py --check  # exit 1 when any of them is stale
```

## Quick start

```bash
python scripts/run_tests.py             # every problem: tests + doctests
python scripts/run_tests.py --only ai   # just the ai problems
python scripts/run_tests.py -v          # one line per test
```

Run one problem on its own:

```bash
cd problems/ai/01-bpe-tokenizer
python -m solution.cli --corpus tests/fixtures/corpus.txt --vocab-size 400 \
    --text "the tokenizer must round trip the same text"
python -m unittest discover -s tests -t .
```

Serve the 3D catalogue (four scenes: token stream, catalogue galaxy, test bars, wireframe):

```bash
make serve        # python3 -m http.server 8000 --directory site
```

No `pip install` and no `npm install` anywhere — the site's Three.js is vendored, so a
fresh clone works offline.

## Layout

```
.
├── problems/
│   ├── ai/
│   │   ├── 01-bpe-tokenizer/{problem.md, solution/, tests/}
│   │   └── 02-retrieval-with-citations/
│   ├── backend/01-token-bucket-rate-limiter/
│   ├── cli/01-file-organizer/
│   ├── coding/01-build-order/
│   ├── data/{01-nginx-log-report, 02-messy-csv-sales-report}/
│   └── network/{01-incremental-http-parser, 02-cidr-planner}/
├── scripts/
│   ├── run_tests.py         # per-problem import isolation + doctests
│   ├── build_catalog.py     # problem.md front matter → catalog.md
│   └── new_problem.py       # scaffold a new problem (red-first)
├── templates/               # problem.md / module / test skeletons
├── site/                    # 3D catalogue (see site/README.md)
│   ├── index.html
│   ├── js/{main,layout,engine}.js + js/scenes/{hero,galaxy,bars,structure}.js
│   ├── data/catalog.js      # generated module the page imports
│   ├── data/problems.json   # the same payload, for tooling
│   ├── assets/three.module.js  # vendored Three.js r169 (MIT)
│   └── tests/               # node --test: layout maths + wiring checks
├── catalog.md               # generated
└── Makefile                 # make test | catalog | site-test | serve | new | clean
```

Each problem folder is the same shape:

```
<NN-slug>/
├── problem.md      # brief: context, requirements (+ why), assumptions, limits
├── solution/
│   ├── main.py     # the logic, with runnable >>> examples in the docstring
│   └── cli.py      # thin argparse wrapper
└── tests/
    ├── test_main.py
    └── fixtures/
```

## Add a problem

```bash
python scripts/new_problem.py network "WebSocket frame decoder" \
    --level hard --concepts "framing, masking, fragmentation"

# or: make new CATEGORY=network TITLE='WebSocket frame decoder'
```

This creates `problem.md`, `solution/{__init__,main}.py`, `tests/{__init__,test_main}.py`
and a fixtures folder, then regenerates the catalog. The stub test **fails on
purpose**: you start from red, describe the behaviour, then make it green.

## Definition of done

1. **A brief somebody else could implement.** `problem.md` has context, input/output,
   numbered requirements with a *why*, an explicit assumptions section and a
   documented-limits section.
2. **Solution code with doctests.** `solution/main.py` carries the reasoning in its
   docstring; runnable `>>>` examples are executed by the test runner.
3. **Tests that would catch the bug you actually fear.** Not just the happy example:
   empty input, malformed input, ties, collisions, clock going backwards, threads,
   bytes split at every possible offset.
4. **A CLI when a human would run it.** `python -m solution.cli --help` must work.
5. **All green.** `python scripts/run_tests.py` passes, and `catalog.md` is regenerated.
6. **Trade-offs are written down.** Example: LRU eviction in the limiter weakens
   fairness, and evicted keys come back with a full bucket — stated, not hidden.

## Design notes

- **`problem.md` is separate from `solution/`** so the brief reads like a ticket and
  the code stays code. The catalog is generated from the brief's front matter, so the
  docs cannot drift from the repository.
- **Front matter is parsed by hand**, not with PyYAML — one more dependency for five
  keys is not worth it (see `scripts/build_catalog.py`).
- **Each problem owns a `solution` package**, which is why the runner builds a clean
  import context per problem instead of one global `unittest discover`.
- **Time and randomness are injected.** The limiter takes a `clock` callable and the
  cross-check tests use fixed seeds, so nothing is flaky and nothing sleeps.
- **Money is `Decimal`, bytes stay bytes.** Two places where "close enough" is a bug
  report: finance code and network framing.
- **Determinism is a feature.** Alphabetical tie-breaks in build order, rank-ordered
  merges in BPE, `(doc_id, start)` ties in retrieval: same input, same output, always.
- **Parity with the stdlib is tested** where it exists (`ipaddress`), so the exercise
  cannot quietly disagree with the language.
- **The website reads generated data, never hand-written copy.** `build_catalog.py`
  emits `site/data/catalog.js` + `site/data/problems.json` (counts included) and
  `--check` fails CI when either is
  stale, so the page cannot claim a problem or a test count that does not exist.
- **Content is DOM, 3D is decoration.** Without WebGL the page hides the canvases and
  still lists every problem; `prefers-reduced-motion` renders a single static frame.

## Roadmap (planned problems)

The six entries that used to be listed here are solved (see the catalogue above).
Next up, in the same style — a brief, a runnable solution, a CLI and a test suite:

- `ai/04` — prompt-budget packer: fit the most useful context into a token window
- `backend/05` — transactional outbox with at-least-once delivery and consumer dedupe
- `cli/02` — a `--watch` runner: debounce, incremental rebuild, clean shutdown
- `coding/03` — JSON Schema validator that reports a path for every failure
- `data/05` — external sort of a file larger than memory, with a stable tie-break
- `network/05` — DNS resolver cache: TTL, negative caching, CNAME chains

## License

[MIT](LICENSE) © sayanoxprivatelimited
