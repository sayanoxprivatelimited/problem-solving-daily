"""Tests for bounded-memory dedupe: order, parity with the naive version, budgets."""
from __future__ import annotations

import io
import json
import os
import random
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import (
    DedupeError,
    Deduper,
    MemoryBudgetExceeded,
    dedupe,
    dedupe_in_memory,
    iter_jsonl,
    read_jsonl,
    write_jsonl,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
EVENTS = os.path.join(FIXTURES, "events.jsonl")


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def deduper(**kwargs: object) -> Deduper:
    options: dict[str, object] = {"key": "id", "shards": 3, "max_keys": 20}
    options.update(kwargs)
    return Deduper(**options)  # type: ignore[arg-type]


class TestBaseline(unittest.TestCase):
    def test_first_occurrence_wins_and_order_is_kept(self) -> None:
        records = [{"id": "b"}, {"id": "a"}, {"id": "b", "n": 2}, {"id": "c"}, {"id": "a", "n": 4}]
        self.assertEqual(
            [record["id"] for record in dedupe_in_memory(records, key="id")], ["b", "a", "c"]
        )

    def test_a_missing_key_is_named_by_line(self) -> None:
        with self.assertRaises(DedupeError) as caught:
            list(dedupe_in_memory([{"id": "a"}, {"nope": 1}], key="id"))
        self.assertIn("line 2", str(caught.exception))


class TestSharded(unittest.TestCase):
    def test_duplicates_are_dropped_and_order_is_global(self) -> None:
        records = [{"id": f"k{index % 5}", "n": index} for index in range(30)]
        survivors = list(dedupe(records, key="id", shards=4, max_keys=50))
        self.assertEqual([record["id"] for record in survivors], ["k0", "k1", "k2", "k3", "k4"])
        self.assertEqual([record["n"] for record in survivors], [0, 1, 2, 3, 4])

    def test_a_duplicate_in_another_shard_is_still_a_duplicate(self) -> None:
        records = [{"id": "same", "n": index} for index in range(12)]
        survivors = list(dedupe(records, key="id", shards=4, max_keys=50))
        self.assertEqual([record["n"] for record in survivors], [0])

    def test_no_duplicates_changes_nothing(self) -> None:
        records = [{"id": f"k{index}", "n": index} for index in range(25)]
        survivors = list(dedupe(records, key="id", shards=4, max_keys=50))
        self.assertEqual(survivors, records)

    def test_empty_input(self) -> None:
        self.assertEqual(list(dedupe([], key="id")), [])
        self.assertEqual(list(dedupe_in_memory([], key="id")), [])

    def test_composite_and_nested_keys(self) -> None:
        records = [
            {"a": 1, "b": 2, "n": 0},
            {"a": 1, "b": 2, "n": 1},
            {"a": 1, "b": 3, "n": 2},
        ]
        survivors = list(dedupe(records, key=lambda record: [record["a"], record["b"]], max_keys=10))
        self.assertEqual([record["n"] for record in survivors], [0, 2])

    def test_a_callable_key_that_raises_is_reported(self) -> None:
        def explode(record: dict) -> object:
            raise KeyError("composite")

        with self.assertRaises(DedupeError):
            list(dedupe([{"n": 1}], key=explode))

    def test_a_missing_key_field_names_the_line(self) -> None:
        with self.assertRaises(DedupeError) as caught:
            list(dedupe([{"id": "a"}, {"id": "b"}, {"nope": 1}], key="id"))
        self.assertIn("line 3", str(caught.exception))

    def test_input_is_consumed_once(self) -> None:
        consumed = []

        def source():
            for index in range(5):
                consumed.append(index)
                yield {"id": f"k{index}"}

        self.assertEqual(len(list(dedupe(source(), key="id", max_keys=10))), 5)
        self.assertEqual(consumed, [0, 1, 2, 3, 4])

    def test_the_shard_path_reproduces_the_naive_result(self) -> None:
        generator = random.Random(99)
        for _ in range(25):
            size = generator.randint(0, 60)
            records = [
                {"id": f"k{generator.randint(0, 19)}", "n": index} for index in range(size)
            ]
            expected = [record["n"] for record in dedupe_in_memory(records, key="id")]
            actual = [
                record["n"]
                for record in dedupe(
                    records,
                    key="id",
                    shards=generator.choice([2, 3, 5]),
                    max_keys=generator.choice([8, 16, 32]),
                )
            ]
            self.assertEqual(actual, expected)


class TestMemoryBudget(unittest.TestCase):
    def test_peak_keys_stay_near_the_budget(self) -> None:
        records = [{"id": f"k{index}", "n": index} for index in range(200)]
        runner = deduper(shards=4, max_keys=8)
        survivors = list(runner.dedupe(records))
        self.assertEqual(len(survivors), 200)
        self.assertLessEqual(runner.stats["peak_keys"], 9)
        self.assertGreater(runner.stats["buckets"], 4)
        self.assertGreaterEqual(runner.stats["reshards"], 1)

    def test_the_output_order_survives_a_re_shard(self) -> None:
        records = [{"id": f"k{index % 40}", "n": index} for index in range(300)]
        survivors = list(dedupe(records, key="id", shards=4, max_keys=8))
        self.assertEqual([record["n"] for record in survivors], list(range(40)))

    def test_a_budget_that_cannot_be_met_is_an_error(self) -> None:
        records = [{"id": f"k{index}"} for index in range(50)]
        with self.assertRaises(MemoryBudgetExceeded):
            list(dedupe(records, key="id", shards=2, max_keys=1, max_depth=0))

    def test_invalid_knobs(self) -> None:
        for kwargs in ({"shards": 1}, {"max_keys": 0}):
            with self.assertRaises(DedupeError):
                Deduper(**kwargs)  # type: ignore[arg-type]

    def test_stats_are_reset_between_runs(self) -> None:
        runner = deduper()
        list(runner.dedupe([{"id": "a"}, {"id": "a"}]))
        first = dict(runner.stats)
        list(runner.dedupe([{"id": "b"}]))
        self.assertEqual(runner.stats["records"], 1)
        self.assertEqual(runner.stats["duplicates"], 0)
        self.assertGreater(first["records"], runner.stats["records"])


class TestTempFiles(unittest.TestCase):
    def test_nothing_is_left_behind_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            list(dedupe([{"id": "a"}, {"id": "b"}], key="id", tmp_dir=sandbox, max_keys=10))
            self.assertEqual(os.listdir(sandbox), [])

    def test_nothing_is_left_behind_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            with self.assertRaises(DedupeError):
                list(
                    dedupe(
                        [{"id": "a"}, {"nope": 1}],
                        key="id",
                        tmp_dir=sandbox,
                        max_keys=10,
                    )
                )
            self.assertEqual(os.listdir(sandbox), [])


class TestJsonlHelpers(unittest.TestCase):
    def test_blank_lines_are_skipped(self) -> None:
        records = list(iter_jsonl(['{"id": 1}\n', "\n", "   \n", '{"id": 2}\n']))
        self.assertEqual(records, [{"id": 1}, {"id": 2}])

    def test_bad_json_names_the_line(self) -> None:
        with self.assertRaises(DedupeError) as caught:
            list(iter_jsonl(['{"id": 1}\n', "{oops\n"], source="events.jsonl"))
        self.assertIn("events.jsonl: line 2", str(caught.exception))

    def test_non_objects_are_refused(self) -> None:
        with self.assertRaises(DedupeError):
            list(iter_jsonl(["[1, 2]\n"]))

    def test_read_and_write_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            path = os.path.join(sandbox, "out.jsonl")
            self.assertEqual(write_jsonl([{"id": "a"}, {"id": "b"}], path), 2)
            self.assertEqual([record["id"] for record in read_jsonl(path)], ["a", "b"])

    def test_the_fixture_dedupes_to_eleven_rows(self) -> None:
        records = list(read_jsonl(EVENTS))
        self.assertEqual(len(records), 40)
        survivors = list(dedupe(records, key="id", max_keys=100))
        self.assertEqual(len(survivors), 11)
        self.assertEqual(len({record["id"] for record in survivors}), 11)


class TestCli(unittest.TestCase):
    def test_dedupes_a_file_to_stdout(self) -> None:
        code, out, _ = run_cli(EVENTS, "--key", "id", "--max-keys", "8")
        self.assertEqual(code, 0)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(len(rows), 11)
        self.assertEqual(len({row["id"] for row in rows}), 11)

    def test_output_order_matches_the_input(self) -> None:
        code, out, _ = run_cli(EVENTS, "--key", "id")
        self.assertEqual(code, 0)
        ids = [json.loads(line)["id"] for line in out.splitlines()]
        expected = []
        for record in read_jsonl(EVENTS):
            if record["id"] not in expected:
                expected.append(record["id"])
        self.assertEqual(ids, expected)

    def test_stats_report_the_budget(self) -> None:
        code, _, err = run_cli(EVENTS, "--key", "id", "--max-keys", "2", "--stats")
        self.assertEqual(code, 0)
        self.assertIn("40 record(s) in", err)
        self.assertIn("11 out", err)
        self.assertIn("sharded", err)

    def test_json_stats(self) -> None:
        code, _, err = run_cli(EVENTS, "--key", "id", "--json", "--max-keys", "4")
        self.assertEqual(code, 0)
        stats = json.loads(err)
        self.assertEqual(stats["records"], 40)
        self.assertEqual(stats["written"], 11)
        self.assertEqual(stats["mode"], "sharded")
        self.assertIn("peak_keys", stats)

    def test_in_memory_mode_agrees(self) -> None:
        _, sharded, _ = run_cli(EVENTS, "--key", "id", "--max-keys", "4")
        _, naive, _ = run_cli(EVENTS, "--key", "id", "--in-memory")
        self.assertEqual(sharded, naive)

    def test_dotted_key_paths(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            path = os.path.join(sandbox, "nested.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"user": {"id": 1}, "n": 0}\n{"user": {"id": 1}, "n": 1}\n')
                handle.write('{"user": {"id": 2}, "n": 2}\n')
            code, out, _ = run_cli(path, "--key", "user.id")
        self.assertEqual(code, 0)
        self.assertEqual([json.loads(line)["n"] for line in out.splitlines()], [0, 2])

    def test_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            target = os.path.join(sandbox, "out.jsonl")
            code, out, _ = run_cli(EVENTS, "--key", "id", "-o", target)
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(len(handle.read().splitlines()), 11)

    def test_a_bad_line_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            path = os.path.join(sandbox, "bad.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"id": 1}\nnot json\n')
            code, _, err = run_cli(path, "--key", "id")
        self.assertEqual(code, 2)
        self.assertIn("invalid JSON", err)

    def test_a_missing_file_exits_two(self) -> None:
        code, _, err = run_cli(os.path.join(FIXTURES, "nope.jsonl"), "--key", "id")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
