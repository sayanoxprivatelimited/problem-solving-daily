"""Tests for the messy sales export: amount/date parsing, cleaning, reporting, CLI."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path

from solution import cli
from solution.main import (
    Sale,
    clean_file,
    clean_rows,
    monthly_report,
    parse_amount,
    parse_date,
    read_csv,
    render,
    write_rejections,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_sales.csv"


class TestParseAmount(unittest.TestCase):
    def test_symbols_and_separators(self) -> None:
        cases = {
            "$1,250.00": (Decimal("1250.00"), "USD"),
            "$980.50": (Decimal("980.50"), "USD"),
            "₹12,500": (Decimal("12500"), "INR"),
            "€2,345.10": (Decimal("2345.10"), "EUR"),
            "USD 42": (Decimal("42"), "USD"),
            "INR 7.25": (Decimal("7.25"), "INR"),
            "Rs. 499.99": (Decimal("499.99"), "INR"),
            "Rs 499.99": (Decimal("499.99"), "INR"),
            "  1000  ": (Decimal("1000"), "UNKNOWN"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), expected)

    def test_parentheses_mean_refund(self) -> None:
        self.assertEqual(parse_amount("(120.00)"), (Decimal("-120.00"), "UNKNOWN"))
        self.assertEqual(parse_amount("($1,000)"), (Decimal("-1000"), "USD"))

    def test_leading_minus(self) -> None:
        self.assertEqual(parse_amount("-$300"), (Decimal("-300"), "USD"))
        self.assertEqual(parse_amount("-(300)"), (Decimal("-300"), "UNKNOWN"))
        self.assertEqual(parse_amount("(-300)"), (Decimal("-300"), "UNKNOWN"))

    def test_bad_input(self) -> None:
        for raw in ("", "   ", None, "$", "abc", "1.2.3", "NaN"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_amount(raw)


class TestParseDate(unittest.TestCase):
    def test_three_formats(self) -> None:
        self.assertEqual(parse_date("2026-11-03"), date(2026, 11, 3))
        self.assertEqual(parse_date("03/11/2026"), date(2026, 11, 3))
        self.assertEqual(parse_date("Nov 5, 2026"), date(2026, 11, 5))
        self.assertEqual(parse_date("  2026-11-03  "), date(2026, 11, 3))

    def test_impossible_dates(self) -> None:
        for raw in ("31/11/2026", "20260103", "", None, "not a date"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_date(raw)


class TestCleanRows(unittest.TestCase):
    def test_fixture_counts(self) -> None:
        result = clean_file(FIXTURE)
        self.assertEqual(len(result.sales), 7)
        self.assertEqual(len(result.rejected), 3)
        self.assertEqual(result.duplicates, ("A-1002",))

    def test_fixture_rejections_explain_themselves(self) -> None:
        result = clean_file(FIXTURE)
        by_id = {item.row["order_id"]: item.reason for item in result.rejected}
        self.assertEqual(set(by_id), {"A-1005", "A-1008", "A-1009"})
        self.assertIn("date", by_id["A-1005"].lower())
        self.assertIn("amount", by_id["A-1008"].lower())
        self.assertIn("number", by_id["A-1009"].lower())

    def test_duplicate_keeps_the_first_row(self) -> None:
        rows = [
            {"order_id": "X1", "date": "2026-11-01", "amount": "$10"},
            {"order_id": "X1", "date": "2026-11-02", "amount": "$99"},
        ]
        result = clean_rows(rows)
        self.assertEqual(len(result.sales), 1)
        self.assertEqual(result.sales[0].amount, Decimal("10"))
        self.assertEqual(result.duplicates, ("X1",))

    def test_invalid_row_does_not_claim_its_id(self) -> None:
        rows = [
            {"order_id": "X2", "date": "nope", "amount": "$10"},
            {"order_id": "X2", "date": "2026-11-02", "amount": "$20"},
        ]
        result = clean_rows(rows)
        self.assertEqual(len(result.sales), 1)
        self.assertEqual(result.sales[0].amount, Decimal("20"))
        self.assertEqual(result.duplicates, ())
        self.assertEqual(len(result.rejected), 1)

    def test_missing_order_id_is_rejected(self) -> None:
        result = clean_rows([{"order_id": "  ", "date": "2026-11-01", "amount": "$5"}])
        self.assertEqual(result.sales, ())
        self.assertIn("order_id", result.rejected[0].reason)

    def test_empty_input(self) -> None:
        result = clean_rows([])
        self.assertEqual(result.sales, ())
        self.assertEqual(result.rejected, ())


class TestMonthlyReport(unittest.TestCase):
    def test_fixture_totals(self) -> None:
        totals = {
            (item.month, item.currency): (item.total, item.orders)
            for item in monthly_report(clean_file(FIXTURE).sales)
        }
        self.assertEqual(
            totals,
            {
                ("2026-11", "INR"): (Decimal("12500"), 1),
                ("2026-11", "UNKNOWN"): (Decimal("-120.00"), 1),
                ("2026-11", "USD"): (Decimal("2305.50"), 3),
                ("2026-12", "EUR"): (Decimal("2345.10"), 1),
                ("2026-12", "INR"): (Decimal("4000"), 1),
            },
        )

    def test_report_is_sorted_by_month_then_currency(self) -> None:
        keys = [(item.month, item.currency) for item in monthly_report(clean_file(FIXTURE).sales)]
        self.assertEqual(keys, sorted(keys))

    def test_money_is_exact(self) -> None:
        pennies = [
            Sale(f"P{index}", date(2026, 11, index + 1), Decimal("0.10"), "USD")
            for index in range(3)
        ]
        total = monthly_report(pennies)[0].total
        self.assertEqual(total, Decimal("0.30"))
        self.assertNotEqual(float(total), 0.1 * 3)  # float would drift here

    def test_single_month_multiple_currencies_are_not_mixed(self) -> None:
        sales = [
            Sale("A", date(2026, 11, 1), Decimal("100"), "USD"),
            Sale("B", date(2026, 11, 2), Decimal("200"), "INR"),
        ]
        totals = monthly_report(sales)
        self.assertEqual([item.currency for item in totals], ["INR", "USD"])


class TestFileHandling(unittest.TestCase):
    def test_read_csv_tolerates_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bom.csv"
            path.write_text("\ufefforder_id,date,amount\nB1,2026-11-01,$5\n", encoding="utf-8")
            self.assertEqual(read_csv(path)[0]["order_id"], "B1")

    def test_write_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rejects.csv"
            count = write_rejections(out, clean_file(FIXTURE).rejected)
            text = out.read_text(encoding="utf-8")
            self.assertEqual(count, 3)
            self.assertIn("_reason", text)
            self.assertIn("A-1005", text)

    def test_render_mentions_every_kind_of_problem(self) -> None:
        text = render(clean_file(FIXTURE))
        self.assertIn("accepted rows    7", text)
        self.assertIn("rejected rows    3", text)
        self.assertIn("A-1002", text)


class TestCli(unittest.TestCase):
    def test_text_output(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main([str(FIXTURE)]), 0)
        self.assertIn("sales report", buffer.getvalue())

    def test_json_output_round_trips(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main([str(FIXTURE), "--json"]), 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(len(payload["sales"]), 7)
        self.assertEqual(payload["duplicates"], ["A-1002"])
        self.assertEqual(payload["monthly"][0]["month"], "2026-11")
        self.assertEqual(payload["monthly"][-1]["total"], "4000")

    def test_rejects_out_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rejects.csv"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([str(FIXTURE), "--rejects-out", str(out)]), 0)
            self.assertTrue(out.exists())

    def test_missing_file(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["nope.csv"]), 1)


if __name__ == "__main__":
    unittest.main()
