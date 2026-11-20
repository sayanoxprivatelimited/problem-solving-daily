"""Tests for session windows: gap rule, merging, watermarks and late data."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import Event, Session, Sessionizer, sessions_from


def spans(sessions: tuple[Session, ...] | list[Session]) -> list[tuple[str, int, int, int]]:
    return [(session.key, session.start, session.end, session.count) for session in sessions]


class TestEvent(unittest.TestCase):
    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            Event(-1, "u")
        with self.assertRaises(ValueError):
            Event(0, "")

    def test_events_are_immutable(self) -> None:
        event = Event(1, "u")
        with self.assertRaises(Exception):
            event.ts = 2  # type: ignore[misc]


class TestGapRule(unittest.TestCase):
    def test_events_inside_the_gap_join_one_session(self) -> None:
        result = sessions_from([Event(0, "u"), Event(5000, "u")], gap_ms=5000)
        self.assertEqual(spans(result), [("u", 0, 5000, 2)])

    def test_the_boundary_is_inclusive(self) -> None:
        self.assertEqual(len(sessions_from([Event(0, "u"), Event(5000, "u")], gap_ms=5000)), 1)
        self.assertEqual(len(sessions_from([Event(0, "u"), Event(5001, "u")], gap_ms=5000)), 2)

    def test_zero_gap_allows_only_identical_timestamps(self) -> None:
        self.assertEqual(spans(sessions_from([Event(7, "u"), Event(7, "u")], gap_ms=0)), [("u", 7, 7, 2)])
        self.assertEqual(len(sessions_from([Event(7, "u"), Event(8, "u")], gap_ms=0)), 2)

    def test_keys_never_mix(self) -> None:
        result = sessions_from([Event(0, "a"), Event(100, "b"), Event(200, "a")], gap_ms=1000)
        self.assertEqual([(session.key, session.count) for session in result], [("a", 2), ("b", 1)])

    def test_result_is_sorted_by_key_then_start(self) -> None:
        result = sessions_from(
            [Event(9000, "b"), Event(0, "b"), Event(500, "a"), Event(9500, "a")],
            gap_ms=100,
            allowed_lateness_ms=10_000,
        )
        self.assertEqual([(session.key, session.start) for session in result], [("a", 500), ("a", 9500), ("b", 0), ("b", 9000)])

    def test_negative_arguments_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Sessionizer(gap_ms=-1)
        with self.assertRaises(ValueError):
            Sessionizer(gap_ms=1, allowed_lateness_ms=-1)


class TestOutOfOrder(unittest.TestCase):
    def test_a_late_event_merges_two_open_sessions(self) -> None:
        # 0 and 6000 are too far apart; 3000 arrives late and bridges them.
        result = sessions_from(
            [Event(0, "u"), Event(6000, "u"), Event(3000, "u")],
            gap_ms=3000,
            allowed_lateness_ms=4000,
        )
        self.assertEqual(spans(result), [("u", 0, 6000, 3)])

    def test_events_are_ordered_inside_a_session(self) -> None:
        result = sessions_from(
            [Event(100, "u"), Event(50, "u"), Event(300, "u")],
            gap_ms=500,
            allowed_lateness_ms=500,
        )
        self.assertEqual([event.ts for event in result[0].events], [50, 100, 300])

    def test_equal_timestamps_keep_arrival_order(self) -> None:
        result = sessions_from(
            [Event(5, "u", "first"), Event(5, "u", "second")], gap_ms=10
        )
        self.assertEqual([event.value for event in result[0].events], ["first", "second"])

    def test_out_of_order_input_matches_sorted_input(self) -> None:
        shuffled = [Event(900, "u"), Event(100, "u"), Event(500, "u"), Event(2000, "u")]
        ordered = sorted(shuffled, key=lambda event: event.ts)
        self.assertEqual(
            spans(sessions_from(shuffled, gap_ms=1000, allowed_lateness_ms=5000)),
            spans(sessions_from(ordered, gap_ms=1000, allowed_lateness_ms=5000)),
        )


class TestWatermark(unittest.TestCase):
    def test_a_session_closes_when_the_watermark_passes_its_gap(self) -> None:
        sessionizer = Sessionizer(gap_ms=1000)
        self.assertEqual(sessionizer.add(Event(0, "u")), [])
        self.assertEqual(sessionizer.add(Event(1000, "u")), [], "merged, but nothing is final yet")
        emitted = sessionizer.add(Event(3000, "u"))
        self.assertEqual(spans(emitted), [("u", 0, 1000, 2)])
        self.assertEqual(sessionizer.open_sessions, 1, "the new event opens its own session")

    def test_lateness_keeps_sessions_open_longer(self) -> None:
        strict = Sessionizer(gap_ms=1000, allowed_lateness_ms=0)
        generous = Sessionizer(gap_ms=1000, allowed_lateness_ms=5000)
        for sessionizer in (strict, generous):
            sessionizer.add(Event(0, "u"))
        self.assertEqual(len(strict.add(Event(2000, "u"))), 1)
        self.assertEqual(len(generous.add(Event(2000, "u"))), 0)

    def test_events_older_than_the_watermark_are_dropped_and_counted(self) -> None:
        sessionizer = Sessionizer(gap_ms=100, allowed_lateness_ms=500)
        sessionizer.add(Event(10000, "u"))
        self.assertEqual(sessionizer.add(Event(9000, "u")), [], "too late to accept")
        self.assertEqual(sessionizer.stats["late_dropped"], 1)
        self.assertEqual(sessionizer.stats["events"], 1)
        self.assertEqual(sessionizer.watermark, 9500)

    def test_watermark_tracks_the_newest_event_minus_lateness(self) -> None:
        sessionizer = Sessionizer(gap_ms=10, allowed_lateness_ms=250)
        self.assertEqual(sessionizer.watermark, 0)
        sessionizer.add(Event(1000, "u"))
        self.assertEqual(sessionizer.watermark, 750)

    def test_emitting_twice_never_repeats_a_session(self) -> None:
        sessionizer = Sessionizer(gap_ms=10)
        events = [Event(0, "u"), Event(100, "u")]
        first = sessionizer.add_all(events)
        second = sessionizer.flush()
        self.assertEqual(spans(first), [("u", 0, 0, 1)])
        self.assertEqual(spans(second), [("u", 100, 100, 1)])
        self.assertEqual(sessionizer.flush(), [], "flush empties the sessionizer")

    def test_stats_report_what_happened(self) -> None:
        sessionizer = Sessionizer(gap_ms=10)
        sessionizer.add_all([Event(0, "u"), Event(100, "u")])
        stats = sessionizer.stats
        self.assertEqual(stats["events"], 2)
        self.assertEqual(stats["sessions_emitted"], 1)
        self.assertEqual(stats["open"], 1)
        sessionizer.flush()
        self.assertEqual(sessionizer.stats["sessions_emitted"], 2)
        self.assertEqual(sessionizer.stats["open"], 0)


class TestSession(unittest.TestCase):
    def test_properties(self) -> None:
        session = sessions_from([Event(100, "u"), Event(400, "u")], gap_ms=500)[0]
        self.assertEqual((session.start, session.end, session.count, session.duration), (100, 400, 2, 300))
        self.assertTrue(session.covers(250))
        self.assertFalse(session.covers(401))

    def test_sessions_are_frozen(self) -> None:
        session = sessions_from([Event(1, "u")], gap_ms=0)[0]
        with self.assertRaises(Exception):
            session.count = 5  # type: ignore[misc]


class TestCli(unittest.TestCase):
    def run_cli(self, argv: list[str], stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        old_stdin = __import__("sys").stdin
        __import__("sys").stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(argv)
        finally:
            __import__("sys").stdin = old_stdin
        return code, out.getvalue(), err.getvalue()

    def test_stdin_jsonl_round_trip(self) -> None:
        stdin = "\n".join(
            json.dumps(event) for event in ({"ts": 0, "key": "u"}, {"ts": 100, "key": "u"})
        )
        code, out, err = self.run_cli(["--gap", "50", "-"], stdin=stdin)
        self.assertEqual(code, 0)
        self.assertIn("u        0", out)
        self.assertIn("2 event(s) accepted", err)

    def test_json_output(self) -> None:
        stdin = json.dumps({"ts": 10, "key": "u", "value": "click"}) + "\n"
        code, out, _ = self.run_cli(["--gap", "0", "newline".replace("newline", "-"), "--json"], stdin=stdin)
        self.assertEqual(code, 0)
        payload = json.loads(out.strip().splitlines()[0])
        self.assertEqual(payload, {"key": "u", "start": 10, "end": 10, "duration": 0, "events": 1})

    def test_file_input(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write(json.dumps({"ts": 1, "key": "u"}) + "\n")
            handle.write("\n")
            handle.write(json.dumps({"ts": 2, "key": "u"}) + "\n")
            path = handle.name
        try:
            code, out, _ = self.run_cli(["--gap", "10", path])
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        self.assertIn("2", out)

    def test_bad_line_reports_the_line_number(self) -> None:
        code, out, err = self.run_cli(["--gap", "10", "-"], stdin='{"ts": 1}\n')
        self.assertEqual(code, 1)
        self.assertIn("line 1", err)
        self.assertEqual(out, "")

    def test_negative_gap_exits_two(self) -> None:
        self.assertEqual(self.run_cli(["--gap", "-5", "-"])[0], 2)


if __name__ == "__main__":
    unittest.main()
