---
title: Cursor pagination over a mutable list
category: backend
level: medium
concepts: pagination, cursors, keyset, snapshots, tamper detection
status: solved
---

# Cursor pagination over a mutable list

## Context

`GET /invoices?page=3` looks harmless until the list changes underneath the reader.
New invoices arrive between the first and second request, rows shift by one, and the
client silently **skips an invoice** — or shows one twice. The bug is invisible in
tests that only ever read an unchanging fixture, and it shows up exactly when it
matters: during a busy day, in an export nobody re-checks.

The fix is to stop paginating by offset. A cursor names a **position** ("everything
after `(sort_key, id)`") and a **snapshot** ("as of sequence 40"), so:

- a row inserted after the reader started cannot appear mid-walk and shift the rest;
- a row deleted mid-walk simply disappears — it cannot cause a duplicate;
- a tampered cursor is rejected, because clients will edit query strings.

## Input / Output

```
input  -> Row(id, sort_key, payload) in a mutable store, a page size, an optional cursor
output -> Page(rows, next_cursor): next_cursor is None on the last page
errors -> InvalidCursor (bad base64, bad signature, unsupported version, bad shape)
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Ordering is `(sort_key, id)` and total: equal sort keys must never reorder | Two invoices with the same date must not swap between pages |
| 2 | A cursor carries the last `(sort_key, id)` **and** the snapshot sequence | Position alone is not enough when rows are inserted |
| 3 | Rows inserted after the first page are invisible for the whole walk | That is the only way to promise "no shifts" |
| 4 | The cursor is signed (`HMAC-SHA256`) and versioned | Clients edit cursors; the server must notice |
| 5 | `next_cursor` is `None` exactly on the last page | Clients loop until `None`, so `has_more` must be truthful |
| 6 | A page size of 0, a negative size or a duplicate id is a programming error | Fail loudly, not with an empty page |

## Constraints & acceptance criteria

- Standard library only; cursors are URL-safe (`A-Za-z0-9-_` plus one `.`).
- `Page.rows` is a tuple of immutable `Row`s; nothing downstream can reorder them.
- The store never sorts the whole table per page: it sorts the snapshot window, which
  is the honest cost of a keyset walk in pure Python (documented limit).
- Deletions may shorten a page; a page is never padded and never repeats a row.
- A cursor is bound to one secret and one version; rotating the secret invalidates
  every outstanding cursor, and that is a feature.

## Assumptions (write them down — real problems are under-specified)

- `sort_key` is an integer (a date truncated to epoch days, a rank, an id from another
  system); the caller decides what "sorted" means.
- `id` is unique and stable. Updating a row's `sort_key` is a delete plus an insert;
  the walk will treat it that way.
- One walk uses one snapshot. A client that resumes tomorrow with an old cursor sees
  the list as it was at that time — that is the documented behaviour, not a bug.
- The secret is the same across instances; a multi-tenant deployment must namespace it.

## How to run

```bash
cd problems/backend/04-cursor-pagination-over-a-mutable-list
python -m unittest discover -s tests -t .
python -m solution.cli tests/fixtures/rows.json --page-size 2
python -m solution.cli tests/fixtures/rows.json --page-size 2 --walk --json
```

## Stretch goals

- Snapshots as an explicit MVCC view (`valid_from`/`valid_to`) instead of a sequence.
- A `total` count that is honest about being approximate during a walk.
- Opaque, shorter cursors (protobuf or a packed struct) once the JSON is the bottleneck.
