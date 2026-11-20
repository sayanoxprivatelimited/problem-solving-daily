"""Clean a client's dirty sales export and report revenue per month per currency.

Three ideas carry this module:

1. **Money is `Decimal`, never `float`.** ``0.1 + 0.2 != 0.3`` in binary floating
   point, and a finance report that is off by a cent is a bug report.
2. **A rejected row explains itself.** Every skip carries a reason, because the
   client will ask "why is order A-1005 missing?".
3. **Guessing is written down.** Missing currency becomes ``UNKNOWN`` and
   ``DD/MM/YYYY`` is assumed instead of silently reordering dates.

Example
-------
>>> parse_amount("$1,250.00")
(Decimal('1250.00'), 'USD')
>>> parse_amount("(120.00)")
(Decimal('-120.00'), 'UNKNOWN')
>>> parse_amount("Rs. 499.99")
(Decimal('499.99'), 'INR')
>>> parse_date("Nov 5, 2026").isoformat()
'2026-11-05'
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping

CURRENCY_SYMBOLS = {"$": "USD", "₹": "INR", "€": "EUR"}
CURRENCY_CODES = {"USD": "USD", "INR": "INR", "EUR": "EUR", "RS.": "INR", "RS": "INR"}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y")
UNKNOWN_CURRENCY = "UNKNOWN"


@dataclass(frozen=True)
class Sale:
    """One trustworthy order line."""

    order_id: str
    date: date
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Rejected:
    """A row we refused to accept, plus why."""

    row: Mapping[str, str]
    reason: str


@dataclass(frozen=True)
class CleanResult:
    """Everything `clean_rows` decided."""

    sales: tuple[Sale, ...]
    rejected: tuple[Rejected, ...]
    duplicates: tuple[str, ...]


@dataclass(frozen=True)
class MonthTotal:
    """Revenue for one (month, currency) bucket."""

    month: str
    currency: str
    total: Decimal
    orders: int


def parse_amount(raw: str | None) -> tuple[Decimal, str]:
    """Parse a human-typed amount into `(Decimal, currency_code)`."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty amount")

    # Accounting notation: "(1,000.00)" is a refund. Parentheses and a leading
    # minus both mean "negative"; they do not cancel each other out.
    negative = False
    if "(" in text or ")" in text:
        negative = True
        text = text.replace("(", "").replace(")", "")
    text = text.strip()
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()

    currency = UNKNOWN_CURRENCY
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            text = text.replace(symbol, " ")
            break
    else:
        upper = text.upper()
        for code in sorted(CURRENCY_CODES, key=len, reverse=True):
            if code in upper:
                currency = CURRENCY_CODES[code]
                index = upper.index(code)
                text = f"{text[:index]} {text[index + len(code):]}"
                break

    text = text.replace(",", "").replace(" ", "").strip()
    if text.startswith("-"):  # e.g. "-$300" only reveals the sign after the symbol
        negative = True
        text = text[1:]

    if not text:
        raise ValueError(f"no digits in amount {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"not a finite number: {raw!r}")

    return (-value if negative else value), currency


def parse_date(raw: str | None) -> date:
    """Parse the three formats the POS export mixes together."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty date")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {raw!r}")


def clean_rows(rows: Iterable[Mapping[str, str]]) -> CleanResult:
    """Validate and deduplicate raw CSV rows, never raising on bad data."""
    sales: list[Sale] = []
    rejected: list[Rejected] = []
    duplicates: list[str] = []
    seen: set[str] = set()

    for row in rows:
        order_id = (row.get("order_id") or "").strip()
        if not order_id:
            rejected.append(Rejected(row, "missing order_id"))
            continue
        if order_id in seen:
            duplicates.append(order_id)
            continue

        try:
            amount, currency = parse_amount(row.get("amount"))
            sale = Sale(
                order_id=order_id,
                date=parse_date(row.get("date")),
                amount=amount,
                currency=currency,
            )
        except ValueError as exc:
            # An untrusted row must not claim its id.
            rejected.append(Rejected(row, str(exc)))
            continue

        seen.add(order_id)
        sales.append(sale)

    return CleanResult(tuple(sales), tuple(rejected), tuple(duplicates))


def monthly_report(sales: Iterable[Sale]) -> list[MonthTotal]:
    """Revenue per (month, currency), sorted for a stable report."""
    totals: dict[tuple[str, str], Decimal] = {}
    counts: dict[tuple[str, str], int] = {}
    for sale in sales:
        key = (sale.date.strftime("%Y-%m"), sale.currency)
        totals[key] = totals.get(key, Decimal("0")) + sale.amount
        counts[key] = counts.get(key, 0) + 1

    return [
        MonthTotal(month=month, currency=currency, total=totals[(month, currency)], orders=counts[(month, currency)])
        for month, currency in sorted(totals)
    ]


def read_csv(path: str | Path) -> list[dict[str, str]]:
    """Read the export as a list of dicts, tolerating a UTF-8 BOM."""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def clean_file(path: str | Path) -> CleanResult:
    """`clean_rows` for a file on disk."""
    return clean_rows(read_csv(path))


def write_rejections(path: str | Path, rejected: Iterable[Rejected]) -> int:
    """Write refused rows to CSV with an extra `_reason` column. Returns the count."""
    rejected = list(rejected)
    fieldnames = ["order_id", "date", "amount", "_reason"]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in rejected:
            row = {key: item.row.get(key, "") for key in fieldnames[:-1]}
            row["_reason"] = item.reason
            writer.writerow(row)
    return len(rejected)


def render(result: CleanResult) -> str:
    """Human-readable summary for the CLI."""
    totals = monthly_report(result.sales)
    lines = [
        "sales report",
        "============",
        f"accepted rows    {len(result.sales)}",
        f"rejected rows    {len(result.rejected)}",
        f"duplicate ids    {len(result.duplicates)}",
        "",
        "month      currency     orders         total",
    ]
    if totals:
        lines += [
            f"{item.month:<10} {item.currency:<10} {item.orders:>6}  {item.total:>13,.2f}"
            for item in totals
        ]
    else:
        lines.append("(no valid rows)")
    if result.duplicates:
        lines += ["", "duplicate order ids: " + ", ".join(sorted(set(result.duplicates)))]
    if result.rejected:
        lines += ["", "rejected rows"]
        lines += [f"  {index + 1}. {item.reason}" for index, item in enumerate(result.rejected[:10])]
        if len(result.rejected) > 10:
            lines.append(f"  … and {len(result.rejected) - 10} more")
    return "\n".join(lines)
