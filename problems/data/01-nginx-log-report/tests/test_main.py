"""Tests for the nginx log report: parser, aggregation, CLI."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timezone
from pathlib import Path

from solution import cli
from solution.main import (
    analyze,
    analyze_file,
    parse_line,
    percentile,
    render,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.log"

VALID_LINE = (
    '203.0.113.10 - - [20/Nov/2026:09:05:11 +0000] "GET /api/users HTTP/1.1" '
    '200 512 "-" "curl/8.0" 0.023'
)


def fixture_lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


class TestParseLine(unittest.TestCase):
    def test_full_line(self) -> None:
        entry = parse_line(VALID_LINE)
        self.assertEqual(entry.ip, "203.0.113.10")
        self.assertEqual(entry.method, "GET")
        self.assertEqual(entry.path, "/api/users")
        self.assertEqual(entry.status, 200)
        self.assertEqual(entry.size, 512)
        self.assertEqual(entry.latency, 0.023)
        self.assertEqual(entry.timestamp.year, 2026)
        self.assertEqual(entry.timestamp.tzinfo, timezone.utc)

    def test_dash_body_size_means_unknown(self) -> None:
        entry = parse_line(VALID_LINE.replace(" 200 512 ", " 204 - "))
        self.assertIsNone(entry.size)

    def test_is_error_boundary(self) -> None:
        self.assertFalse(parse_line(VALID_LINE).is_error)
        self.assertFalse(parse_line(VALID_LINE.replace(" 200 ", " 399 ")).is_error)
        self.assertTrue(parse_line(VALID_LINE.replace(" 200 ", " 400 ")).is_error)
        self.assertTrue(parse_line(VALID_LINE.replace(" 200 ", " 500 ")).is_error)

    def test_rejects_garbage(self) -> None:
        for bad in (
            "healthcheck failed: connection refused",
            '203.0.113.99 - - [20/Nov/2026:12:00:00 +0000] "GET /x HTTP/1.1" 200 512 "-" "curl"',
            "198.51.100.7 - - [20/Nov/2026:12:0",
            "",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_line(bad)

    def test_rejects_impossible_date(self) -> None:
        bad_date = VALID_LINE.replace("20/Nov/2026:09:05:11", "32/Nov/2026:09:05:11")
        with self.assertRaises(ValueError):
            parse_line(bad_date)


class TestPercentile(unittest.TestCase):
    def test_nearest_rank(self) -> None:
        values = [1, 2, 3, 4]
        self.assertEqual(percentile(values, 0.5), 2)
        self.assertEqual(percentile(values, 0.95), 4)
        self.assertEqual(percentile(values, 1.0), 4)
        self.assertEqual(percentile([7.0], 0.95), 7.0)

    def test_unsorted_input_is_sorted_first(self) -> None:
        # sorted -> [1.0, 5.0, 9.0]; rank = ceil(0.2 * 3) = 1 and ceil(0.34 * 3) = 2
        self.assertEqual(percentile([9.0, 1.0, 5.0], 0.2), 1.0)
        self.assertEqual(percentile([9.0, 1.0, 5.0], 0.34), 5.0)

    def test_rejects_bad_arguments(self) -> None:
        with self.assertRaises(ValueError):
            percentile([], 0.95)
        with self.assertRaises(ValueError):
            percentile([1.0], 0.0)
        with self.assertRaises(ValueError):
            percentile([1.0], 1.5)


class TestAnalyze(unittest.TestCase):
    def test_fixture_report(self) -> None:
        report = analyze(fixture_lines())
        self.assertEqual(report.total_requests, 10)
        self.assertEqual(report.malformed_lines, 3)
        self.assertEqual(report.unique_clients, 5)
        self.assertAlmostEqual(report.error_rate, 0.4)
        self.assertAlmostEqual(report.p95_latency_ms, 1203.0)
        self.assertEqual(report.busiest_hour, "2026-11-20 11:00")
        self.assertEqual(report.busiest_hour_hits, 4)
        self.assertEqual(
            report.top_paths,
            (
                ("/api/users", 3),
                ("/api/orders", 2),
                ("/health", 2),
                ("/api/login", 1),
                ("/assets/app.js", 1),
            ),
        )

    def test_top_n_truncates(self) -> None:
        self.assertEqual(len(analyze(fixture_lines(), top_n=2).top_paths), 2)
        self.assertEqual(analyze(fixture_lines(), top_n=0).top_paths, ())

    def test_blank_lines_are_ignored_not_counted_as_malformed(self) -> None:
        report = analyze(["", "   ", "\t", VALID_LINE])
        self.assertEqual(report.total_requests, 1)
        self.assertEqual(report.malformed_lines, 0)

    def test_empty_log(self) -> None:
        report = analyze([])
        self.assertEqual(report.total_requests, 0)
        self.assertEqual(report.error_rate, 0.0)
        self.assertEqual(report.p95_latency_ms, 0.0)
        self.assertIsNone(report.busiest_hour)
        self.assertIn("(no requests)", render(report))

    def test_accepts_generator_without_buffering(self) -> None:
        def stream():
            for line in fixture_lines():
                yield line

        self.assertEqual(analyze(stream()).total_requests, 10)

    def test_busiest_hour_tie_takes_earliest(self) -> None:
        early = VALID_LINE.replace("09:05:11", "08:00:00")
        late = VALID_LINE.replace("09:05:11", "23:59:59")
        self.assertEqual(analyze([early, late]).busiest_hour, "2026-11-20 08:00")

    def test_all_malformed(self) -> None:
        report = analyze(["garbage", "more garbage"])
        self.assertEqual(report.total_requests, 0)
        self.assertEqual(report.malformed_lines, 2)

    def test_analyze_file_streams_the_fixture(self) -> None:
        self.assertEqual(analyze_file(FIXTURE).total_requests, 10)


class TestRenderAndCli(unittest.TestCase):
    def test_render_contains_the_headline_numbers(self) -> None:
        text = render(analyze(fixture_lines()))
        self.assertIn("requests        10", text)
        self.assertIn("40.0%", text)
        self.assertIn("1203.0 ms", text)
        self.assertIn("/api/users", text)

    def test_cli_text_output(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = cli.main([str(FIXTURE), "--top", "2"])
        self.assertEqual(exit_code, 0)
        self.assertIn("top paths", buffer.getvalue())

    def test_cli_json_output(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = cli.main([str(FIXTURE), "--json", "--top", "2"])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["total_requests"], 10)
        self.assertEqual(payload["busiest_hour"], "2026-11-20 11:00")
        self.assertEqual(len(payload["top_paths"]), 2)

    def test_cli_reports_missing_file(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["does-not-exist.log"]), 1)

    def test_cli_rejects_negative_top(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(FIXTURE), "--top", "-1"]), 2)


if __name__ == "__main__":
    unittest.main()
