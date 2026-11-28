"""Tests for the watermark-driven windowed aggregator: windows, lateness, retractions.

The interesting cases are all adversarial: a watermark that must never go backwards, an
event that arrives after its window was emitted, a straggler that is late but *not* too
late, a sliding window that the same event lands in twice, and a stream whose arrival
order is shuffled but whose answer must not change.
"""
from __future__ import annotations

import io
import json
import random
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from solution import cli
from solution.main import (
    AGGREGATES,
    AggregationError,
    Aggregator,
    Event,
    Record,
    aggregate,
    iter_events,
    net_totals,
)

PROBLEM = Path(__file__).resolve().parent.parent


def windows(records: list[Record]) -> list[tuple[str, float, int]]:
    """(key, window_start, count) for the non-retraction records only."""
    return [(r.key, r.window_start, r.count) for r in records if not r.retracted]


class ValidationTests(unittest.TestCase):
    """Bad configuration fails loudly at construction, not halfway through a stream."""

    def test_size_must_be_positive(self) -> None:
        for size in (0, -1, -0.5):
            with self.assertRaises(AggregationError):
                Aggregator(size=size)

    def test_hop_must_be_positive(self) -> None:
        with self.assertRaises(AggregationError):
            Aggregator(size=10, hop=0)

    def test_hop_larger_than_size_is_rejected(self) -> None:
        # hop > size leaves holes: some timestamps would belong to no window at all.
        with self.assertRaises(AggregationError):
            Aggregator(size=10, hop=20)

    def test_lateness_must_be_non_negative(self) -> None:
        with self.assertRaises(AggregationError):
            Aggregator(size=10, lateness=-1)

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(AggregationError):
            Aggregator(size=10, mode="upsert")  # type: ignore[arg-type]

    def test_unknown_aggregate_is_rejected(self) -> None:
        with self.assertRaises(AggregationError):
            Aggregator(size=10, aggregates=("count", "median"))

    def test_all_documented_aggregates_are_accepted(self) -> None:
        Aggregator(size=10, aggregates=AGGREGATES)

    def test_add_rejects_non_events(self) -> None:
        with self.assertRaises(AggregationError):
            Aggregator(size=10).add({"key": "a", "ts": 1})  # type: ignore[arg-type]

    def test_event_key_must_be_a_string(self) -> None:
        with self.assertRaises(AggregationError):
            Event(key=1, ts=0)  # type: ignore[arg-type]

    def test_event_ts_must_be_a_number(self) -> None:
        for ts in ("1", None, True, [1]):
            with self.assertRaises(AggregationError):
                Event(key="a", ts=ts)  # type: ignore[arg-type]

    def test_event_ts_must_be_finite(self) -> None:
        for ts in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(AggregationError):
                Event(key="a", ts=ts)

    def test_event_value_must_be_a_finite_number(self) -> None:
        for value in ("1", None, float("nan")):
            with self.assertRaises(AggregationError):
                Event(key="a", ts=0, value=value)  # type: ignore[arg-type]

    def test_event_id_must_be_a_string(self) -> None:
        with self.assertRaises(AggregationError):
            Event(key="a", ts=0, id=7)  # type: ignore[arg-type]


class TumblingWindowTests(unittest.TestCase):
    """hop == size: one window per period, emitted when the watermark reaches its end."""

    def test_nothing_is_emitted_before_the_watermark_moves(self) -> None:
        agg = Aggregator(size=10)
        self.assertEqual(agg.add(Event("a", 1)), [])
        self.assertEqual(agg.add(Event("a", 2)), [])
        self.assertEqual(agg.windows_open, 1)

    def test_the_event_that_moves_the_watermark_emits_the_previous_window(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1))
        agg.add(Event("a", 2))
        records = agg.add(Event("a", 11))
        self.assertEqual([(r.window_start, r.window_end, r.count) for r in records], [(0.0, 10.0, 2)])
        self.assertFalse(records[0].retracted)

    def test_flush_emits_the_last_window(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1))
        self.assertEqual([r.count for r in agg.flush()], [1])

    def test_flush_twice_emits_nothing_the_second_time(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1))
        self.assertEqual(len(agg.flush()), 1)
        self.assertEqual(agg.flush(), [])

    def test_empty_stream_produces_nothing(self) -> None:
        self.assertEqual(aggregate([], size=10), [])

    def test_boundaries_are_half_open(self) -> None:
        # ts == 10 belongs to [10, 20), not to [0, 10).
        records = aggregate([Event("a", 10)], size=10)
        self.assertEqual([(r.window_start, r.count) for r in records], [(10.0, 1)])

    def test_a_gap_of_time_makes_an_empty_window_disappear_not_appear(self) -> None:
        # No events in [10, 20) means no [10, 20) record at all: we emit data, not silence.
        records = aggregate([Event("a", 1), Event("a", 25)], size=10)
        self.assertEqual([r.window_start for r in records], [0.0, 20.0])

    def test_very_large_timestamps_stay_exact(self) -> None:
        base = 1_730_000_000_000
        records = aggregate([Event("a", base + 1), Event("a", base + 2)], size=1000)
        self.assertEqual(records[0].window_start, (base // 1000) * 1000)

    def test_fractional_timestamps_are_supported(self) -> None:
        records = aggregate([Event("a", 0.5), Event("a", 1.5)], size=10)
        self.assertEqual(records[0].window_start, 0.0)
        self.assertEqual(records[0].count, 2)


class SlidingWindowTests(unittest.TestCase):
    """hop < size: one event lands in several windows, and that is the point."""

    def test_one_event_belongs_to_two_windows(self) -> None:
        # hop=5, size=10: ts=7 is inside [0,10) and [5,15).
        records = aggregate([Event("a", 7), Event("a", 100)], size=10, hop=5)
        starts = [r.window_start for r in records if r.count == 1]
        self.assertIn(0.0, starts)
        self.assertIn(5.0, starts)

    def test_sliding_counts_sum_to_more_than_the_event_count(self) -> None:
        events = [Event("a", ts) for ts in range(0, 20)]
        records = aggregate(events, size=10, hop=5)
        self.assertGreater(sum(r.count for r in records), len(events))

    def test_sliding_emission_is_ordered_by_window_end(self) -> None:
        records = aggregate([Event("a", ts) for ts in range(0, 50)], size=10, hop=5)
        ends = [r.window_end for r in records]
        self.assertEqual(ends, sorted(ends))

    def test_hop_equal_to_size_stays_tumbling(self) -> None:
        tumbling = aggregate([Event("a", ts) for ts in range(0, 20)], size=10)
        explicit = aggregate([Event("a", ts) for ts in range(0, 20)], size=10, hop=10)
        self.assertEqual(tumbling, explicit)


class WatermarkTests(unittest.TestCase):
    """The watermark is a promise, and it only moves forward."""

    def test_watermark_starts_as_none(self) -> None:
        self.assertIsNone(Aggregator(size=10).watermark)

    def test_watermark_follows_the_max_timestamp(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 5))
        self.assertEqual(agg.watermark, 5)
        agg.add(Event("a", 50))
        self.assertEqual(agg.watermark, 50)

    def test_watermark_never_moves_backwards(self) -> None:
        agg = Aggregator(size=10, lateness=5)
        agg.add(Event("a", 50))
        agg.add(Event("a", 5))
        self.assertEqual(agg.watermark, 45)

    def test_lateness_delays_emission_by_exactly_that_much(self) -> None:
        # Window [0,10) closes when max_ts >= 10 + lateness.
        agg = Aggregator(size=10, lateness=5)
        agg.add(Event("a", 1))
        self.assertEqual(agg.add(Event("a", 10)), [])
        self.assertEqual(agg.add(Event("a", 14)), [])
        self.assertEqual([r.count for r in agg.add(Event("a", 15))], [1])

    def test_lateness_zero_closes_as_soon_as_the_end_is_reached(self) -> None:
        agg = Aggregator(size=10, lateness=0)
        agg.add(Event("a", 1))
        self.assertEqual(len(agg.add(Event("a", 10))), 1)


class LateDataTests(unittest.TestCase):
    """Late data: dropped loudly, or corrected in the open."""

    def test_late_but_open_data_is_counted(self) -> None:
        # lateness=20 keeps the watermark at 5, so [0,10) is still open when ts=5 arrives.
        agg = Aggregator(size=10, lateness=20)
        agg.add(Event("a", 25))
        self.assertEqual(agg.add(Event("a", 2)), [])
        self.assertEqual(agg.add(Event("a", 5)), [])
        self.assertEqual(windows(agg.flush()), [("a", 0.0, 2), ("a", 20.0, 1)])

    def test_lateness_only_buys_time_up_to_its_own_value(self) -> None:
        # With lateness=10 and max_ts=25 the watermark is 15, so [0,10) is already gone.
        agg = Aggregator(size=10, lateness=10)
        agg.add(Event("a", 5))
        self.assertEqual([r.count for r in agg.add(Event("a", 25))], [1])

    def test_drop_mode_discards_and_counts(self) -> None:
        agg = Aggregator(size=10, lateness=0, mode="drop")
        agg.add(Event("a", 1))
        agg.add(Event("a", 25))  # emits [0,10) with count 1
        self.assertEqual(agg.add(Event("a", 5)), [])
        self.assertEqual(agg.stats.dropped_late, 1)

    def test_drop_mode_keeps_the_closed_aggregate_untouched(self) -> None:
        agg = Aggregator(size=10, lateness=0, mode="drop")
        agg.add(Event("a", 1))
        agg.add(Event("a", 25))
        agg.add(Event("a", 5))
        self.assertEqual(agg.flush()[0].count, 1)

    def test_retract_mode_emits_a_retraction_then_the_correction(self) -> None:
        agg = Aggregator(size=10, lateness=0)
        agg.add(Event("a", 1))
        agg.add(Event("a", 25))
        records = agg.add(Event("a", 5))
        self.assertEqual([(r.count, r.retracted) for r in records], [(1, True), (2, False)])
        self.assertEqual(agg.stats.retractions, 1)

    def test_retraction_comes_before_the_replacement(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1))
        agg.add(Event("a", 25))
        records = agg.add(Event("a", 5))
        self.assertTrue(records[0].retracted)
        self.assertFalse(records[1].retracted)

    def test_the_retracted_values_are_the_old_ones(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1, value=4))
        agg.add(Event("a", 25))
        retraction, correction = agg.add(Event("a", 5, value=10))
        self.assertEqual((retraction.total, retraction.count), (4.0, 1))
        self.assertEqual((correction.total, correction.count), (14.0, 2))

    def test_two_stragglers_retract_twice(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1))
        agg.add(Event("a", 25))
        agg.add(Event("a", 5))
        records = agg.add(Event("a", 6))
        self.assertEqual([r.retracted for r in records], [True, False])
        self.assertEqual(records[-1].count, 3)
        self.assertEqual(agg.stats.retractions, 2)

    def test_net_totals_apply_retractions(self) -> None:
        records = aggregate([Event("a", 1), Event("a", 25), Event("a", 5)], size=10, lateness=0)
        self.assertEqual(net_totals(records)[("a", 0.0)], 2.0)

    def test_net_totals_of_a_clean_stream_are_the_sums(self) -> None:
        records = aggregate([Event("a", ts, value=2) for ts in range(0, 5)], size=10)
        self.assertEqual(net_totals(records)[("a", 0.0)], 10.0)

    def test_raw_sum_over_retracting_records_is_wrong_on_purpose(self) -> None:
        # A consumer that ignores ``retracted`` double-counts; net_totals is the fix.
        records = aggregate([Event("a", 1), Event("a", 25), Event("a", 5)], size=10)
        self.assertEqual(sum(r.total for r in records), 5.0)
        self.assertEqual(net_totals(records)[("a", 0.0)], 2.0)


class AggregateValueTests(unittest.TestCase):
    """count / sum / min / max / mean, including the awkward numbers."""

    def test_default_value_makes_sum_a_row_count(self) -> None:
        record = aggregate([Event("a", 1), Event("a", 2)], size=10)[0]
        self.assertEqual((record.count, record.total), (2, 2.0))

    def test_min_max_and_mean(self) -> None:
        record = aggregate([Event("a", 1, value=5), Event("a", 2, value=-3), Event("a", 3, value=4)], size=10)[0]
        self.assertEqual((record.minimum, record.maximum), (-3, 5))
        self.assertAlmostEqual(record.mean or 0, 2.0)

    def test_negative_values_are_aggregated_not_ignored(self) -> None:
        record = aggregate([Event("a", 1, value=-1), Event("a", 2, value=-2)], size=10)[0]
        self.assertEqual((record.total, record.minimum), (-3.0, -2))

    def test_zero_values_still_count(self) -> None:
        record = aggregate([Event("a", 1, value=0)], size=10)[0]
        self.assertEqual((record.count, record.total, record.minimum), (1, 0.0, 0))

    def test_mean_of_mixed_signs(self) -> None:
        record = aggregate([Event("a", 1, value=10), Event("a", 2, value=-2)], size=10)[0]
        self.assertAlmostEqual(record.mean or 0, 4.0)

    def test_as_dict_is_json_ready(self) -> None:
        record = aggregate([Event("a", 1)], size=10)[0]
        payload = json.loads(json.dumps(record.as_dict()))
        self.assertEqual(payload["window_start"], 0)
        self.assertEqual(payload["count"], 1)

    def test_records_are_frozen(self) -> None:
        record = aggregate([Event("a", 1)], size=10)[0]
        with self.assertRaises(Exception):
            record.count = 5  # type: ignore[misc]


class KeyTests(unittest.TestCase):
    """Keys partition the world; ordering must not depend on arrival order."""

    def test_two_keys_get_two_windows(self) -> None:
        records = aggregate([Event("a", 1), Event("b", 2)], size=10)
        self.assertEqual(sorted(r.key for r in records), ["a", "b"])

    def test_same_key_different_windows(self) -> None:
        records = aggregate([Event("a", 1), Event("a", 12)], size=10)
        self.assertEqual([r.count for r in records], [1, 1])

    def test_keys_are_sorted_within_a_window_end(self) -> None:
        records = aggregate([Event("c", 1), Event("a", 1), Event("b", 1)], size=10)
        self.assertEqual([r.key for r in records], ["a", "b", "c"])

    def test_unicode_keys_survive(self) -> None:
        records = aggregate([Event("কী-বোর্ড", 1)], size=10)
        self.assertEqual(records[0].key, "কী-বোর্ড")

    def test_empty_key_is_allowed(self) -> None:
        self.assertEqual(aggregate([Event("", 1)], size=10)[0].key, "")


class DedupeTests(unittest.TestCase):
    """A redelivered event is a delivery problem, not a value."""

    def test_duplicate_id_is_counted_once(self) -> None:
        records = aggregate([Event("a", 1, id="x"), Event("a", 1, id="x")], size=10)
        self.assertEqual(records[0].count, 1)

    def test_duplicate_is_counted_in_stats(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1, id="x"))
        agg.add(Event("a", 1, id="x"))
        self.assertEqual(agg.stats.duplicates, 1)
        self.assertEqual(agg.stats.events_in, 1)

    def test_dedupe_can_be_switched_off(self) -> None:
        agg = Aggregator(size=10, dedupe=False)
        agg.add(Event("a", 1, id="x"))
        agg.add(Event("a", 1, id="x"))
        self.assertEqual(agg.flush()[0].count, 2)

    def test_events_without_ids_are_never_deduped(self) -> None:
        records = aggregate([Event("a", 1), Event("a", 1)], size=10)
        self.assertEqual(records[0].count, 2)

    def test_duplicate_across_windows_is_still_a_duplicate(self) -> None:
        agg = Aggregator(size=10)
        agg.add(Event("a", 1, id="x"))
        self.assertEqual(agg.add(Event("b", 99, id="x")), [])


class StatsTests(unittest.TestCase):
    """Counters exist so that data loss is visible rather than suspiciously quiet."""

    def test_events_in_counts_unique_events(self) -> None:
        agg = Aggregator(size=10)
        for ts in range(3):
            agg.add(Event("a", ts))
        self.assertEqual(agg.stats.events_in, 3)

    def test_windows_emitted_and_records_out(self) -> None:
        agg = Aggregator(size=10)
        agg.extend([Event("a", 1), Event("a", 11), Event("a", 21)])
        self.assertEqual(agg.stats.windows_emitted, 2)
        self.assertEqual(agg.stats.records_out, 2)

    def test_stats_are_serialisable(self) -> None:
        payload = Aggregator(size=10).stats.as_dict()
        self.assertEqual(
            sorted(payload),
            ["dropped_late", "duplicates", "events_in", "records_out", "retractions", "windows_emitted"],
        )

    def test_open_and_closed_windows_add_up(self) -> None:
        agg = Aggregator(size=10)
        for ts in range(0, 100, 3):
            agg.add(Event("a", ts))
        self.assertEqual((agg.windows_open, agg.windows_closed), (1, 9))


class MemoryTests(unittest.TestCase):
    """Bounded memory is the reason this problem exists at all."""

    def test_emitted_windows_leave_the_open_set(self) -> None:
        agg = Aggregator(size=10)
        for ts in range(0, 1000):
            agg.add(Event("a", ts))
        self.assertLessEqual(agg.windows_open, 2)

    def test_a_million_events_do_not_grow_the_open_set(self) -> None:
        agg = Aggregator(size=1000, lateness=10)
        for ts in range(0, 100_000, 7):
            agg.add(Event("a", ts))
        self.assertLessEqual(agg.windows_open, 3)

    def test_closed_state_is_kept_for_retraction_only_when_asked(self) -> None:
        drop = Aggregator(size=10, mode="drop")
        retract = Aggregator(size=10, mode="retract")
        for agg in (drop, retract):
            agg.extend([Event("a", ts) for ts in range(0, 50)])
        self.assertEqual(drop.windows_closed, retract.windows_closed)


class DeterminismTests(unittest.TestCase):
    """Same events, different arrival order, same answer."""

    def test_identical_input_gives_identical_output(self) -> None:
        events = [Event("a", ts) for ts in range(0, 100, 3)]
        self.assertEqual(aggregate(events, size=10), aggregate(events, size=10))

    def test_shuffled_arrival_with_enough_lateness_gives_the_same_totals(self) -> None:
        events = [Event("a", ts, value=ts) for ts in range(0, 50)]
        ordered = aggregate(events, size=10, lateness=1000)
        shuffled = list(events)
        random.Random(1234).shuffle(shuffled)
        self.assertEqual(net_totals(ordered), net_totals(aggregate(shuffled, size=10, lateness=1000)))

    def test_shuffled_arrival_changes_the_stream_but_not_the_net_answer(self) -> None:
        events = [Event("a", ts) for ts in range(0, 30)]
        shuffled = list(events)
        random.Random(7).shuffle(shuffled)
        self.assertEqual(net_totals(aggregate(events, size=10)), net_totals(aggregate(shuffled, size=10)))

    def test_extend_and_add_produce_the_same_records(self) -> None:
        events = [Event("a", ts) for ts in range(0, 40)]
        one_by_one: list[Record] = []
        agg = Aggregator(size=10)
        for event in events:
            one_by_one.extend(agg.add(event))
        one_by_one.extend(agg.flush())
        self.assertEqual(one_by_one, aggregate(events, size=10))


class IterEventsTests(unittest.TestCase):
    """The JSON boundary gets the same care as the maths."""

    def test_defaults_are_applied(self) -> None:
        events = list(iter_events([{"key": "a", "ts": 1}]))
        self.assertEqual(events[0].value, 1.0)
        self.assertIsNone(events[0].id)

    def test_missing_field_names_the_row(self) -> None:
        with self.assertRaises(AggregationError) as caught:
            list(iter_events([{"key": "a", "ts": 1}, {"key": "b"}]))
        self.assertIn("row 1", str(caught.exception))
        self.assertIn("'ts'", str(caught.exception))

    def test_bad_type_names_the_row(self) -> None:
        with self.assertRaises(AggregationError) as caught:
            list(iter_events([{"key": "a", "ts": "soon"}]))
        self.assertIn("row 0", str(caught.exception))


class CliTests(unittest.TestCase):
    """The CLI is the only part a human touches, so it gets the same tests as the maths."""

    def run_cli(self, argv: list[str], stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def write(self, rows: list[dict[str, object]]) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        with handle as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_reads_a_file_and_prints_jsonl(self) -> None:
        path = self.write([{"key": "a", "ts": 1}, {"key": "a", "ts": 2}])
        code, out, _ = self.run_cli([path, "--size", "10"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.strip())["count"], 2)

    def test_reads_stdin(self) -> None:
        code, out, _ = self.run_cli(["-", "--size", "10"], stdin='{"key": "a", "ts": 1}\n')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.strip())["key"], "a")

    def test_tsv_format_is_tab_separated(self) -> None:
        code, out, _ = self.run_cli(["-", "--size", "10", "--format", "tsv"], stdin='{"key": "a", "ts": 1}\n')
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().split("\t"), ["a", "0", "10", "1", "1", "emit"])

    def test_stats_go_to_stderr(self) -> None:
        code, out, err = self.run_cli(["-", "--size", "10", "--stats"], stdin='{"key": "a", "ts": 1}\n')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(err.strip())["events_in"], 1)
        self.assertNotIn("events_in", out)

    def test_blank_lines_are_skipped(self) -> None:
        code, out, _ = self.run_cli(["-", "--size", "10"], stdin='\n{"key": "a", "ts": 1}\n\n')
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_empty_input_prints_nothing(self) -> None:
        code, out, _ = self.run_cli(["-", "--size", "10"])
        self.assertEqual((code, out), (0, ""))

    def test_malformed_json_is_exit_2(self) -> None:
        code, _, err = self.run_cli(["-", "--size", "10"], stdin="not json\n")
        self.assertEqual(code, 2)
        self.assertIn("line 1", err)

    def test_missing_field_is_exit_2(self) -> None:
        code, _, err = self.run_cli(["-", "--size", "10"], stdin='{"key": "a"}\n')
        self.assertEqual(code, 2)
        self.assertIn("row 0", err)

    def test_non_object_row_is_exit_2(self) -> None:
        code, _, err = self.run_cli(["-", "--size", "10"], stdin="[1, 2]\n")
        self.assertEqual(code, 2)
        self.assertIn("expected an object", err)

    def test_unreadable_file_is_exit_2(self) -> None:
        code, _, err = self.run_cli(["/nope/missing.jsonl", "--size", "10"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_bad_size_is_exit_2(self) -> None:
        code, _, err = self.run_cli(["-", "--size", "0"], stdin="")
        self.assertEqual(code, 2)
        self.assertIn("size", err)

    def test_drop_mode_reports_dropped_late(self) -> None:
        path = self.write([{"key": "a", "ts": 1}, {"key": "a", "ts": 25}, {"key": "a", "ts": 5}])
        code, _, err = self.run_cli([path, "--size", "10", "--mode", "drop", "--stats"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(err.strip())["dropped_late"], 1)

    def test_no_dedupe_flag_counts_twice(self) -> None:
        path = self.write([{"key": "a", "ts": 1, "id": "x"}, {"key": "a", "ts": 1, "id": "x"}])
        code, out, _ = self.run_cli([path, "--size", "10", "--no-dedupe"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.strip())["count"], 2)

    def test_help_exits_zero(self) -> None:
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.build_parser().parse_args(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_module_entry_point_runs(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "solution.cli", "--help"],
            cwd=PROBLEM,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--lateness", result.stdout)


if __name__ == "__main__":
    unittest.main()
