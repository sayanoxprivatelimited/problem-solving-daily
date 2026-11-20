---
title: Clean a messy sales export
category: data
level: medium
concepts: CSV parsing, Decimal money math, multi-format dates, dedupe, data quality
status: solved
---

# Clean a messy sales export

## Context

A client sends their sales export every Monday. It comes out of an old POS system
and it is *dirty*: the same order appears twice because someone re-ran the export,
amounts are typed the way humans type them (`$1,250.00`, `₹12,500`, `(120.00)` for
a refund), and dates switch between three formats inside the same file. Blank and
garbage cells are sprinkled in.

Finance needs one thing: **revenue per month per currency**, and a list of the rows
you refused to accept — with reasons.

## Input

`sample_sales.csv` with the header `order_id,date,amount`:

```csv
order_id,date,amount
A-1001,2026-11-03,"$1,250.00"
A-1002,03/11/2026,$980.50
A-1003,"Nov 5, 2026","₹12,500"
A-1002,2026-11-03,"$1,250.00"
```

## Output

A `CleanResult` with three parts:

1. `sales` — deduplicated, fully typed `Sale(order_id, date, amount, currency)` rows
2. `rejected` — `(row, reason)` for every row that could not be trusted
3. `duplicates` — order ids seen more than once

plus `monthly_report(sales) -> [MonthTotal(month, currency, total, orders)]`,
sorted by `(month, currency)`.

## Business rules (these are the whole problem)

| Rule | Behaviour |
|------|-----------|
| Duplicate `order_id` | keep the **first** accepted row, record the id in `duplicates` |
| An invalid row never claims its id | a later valid row with the same id is still accepted |
| Amounts | support `$ ₹ €`, `USD/INR/EUR/Rs./Rs`, thousands separators, `(…)` = negative, leading `-` |
| Missing currency | bucket as `UNKNOWN` instead of guessing |
| Money math | `Decimal`, never `float` — a report that is off by a cent is a bug report |
| Dates | `YYYY-MM-DD`, `DD/MM/YYYY`, `Mon D, YYYY` |
| Bad date / empty / non-numeric amount | reject with a human-readable reason |

## Constraints & acceptance criteria

- Blank cells reject the row instead of raising out of `clean_rows`.
- `DD/MM/YYYY` is assumed (the client is not in the US) — **documented as an
  assumption**, because `03/11/2026` is genuinely ambiguous.
- Totals are exact: `0.10 + 0.20` must equal `Decimal("0.30")`.
- `python -m solution.cli tests/fixtures/sample_sales.csv` prints the report,
  `--json` emits structured output, `--rejects-out out.csv` writes the refused rows.

## How to run

```bash
cd problems/data/02-messy-csv-sales-report
python -m solution.cli tests/fixtures/sample_sales.csv
python -m solution.cli tests/fixtures/sample_sales.csv --json
python -m unittest discover -s tests -t .
```

## Stretch goals

- Support `1.234,56` (European decimal comma) by detecting which separator repeats.
- Add an FX table so all months report in one currency.
- Detect duplicate *content* (same date+amount, different id) and flag as suspicious.
