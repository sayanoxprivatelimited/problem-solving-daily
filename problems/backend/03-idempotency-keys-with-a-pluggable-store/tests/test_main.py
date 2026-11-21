"""Tests for idempotency keys: replay, conflicts, expiry, failure and races."""
from __future__ import annotations

import io
import json
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from solution import cli
from solution.main import (
    FakeClock,
    IdempotencyConflict,
    IdempotencyError,
    IdempotencyLayer,
    MemoryStore,
    Record,
    RequestInFlight,
    Result,
)


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def make(**kwargs: Any) -> tuple[IdempotencyLayer, FakeClock, list[int]]:
    clock = kwargs.pop("clock", None) or FakeClock(0)
    layer = IdempotencyLayer(MemoryStore(clock), clock, **kwargs)
    calls: list[int] = []

    def handler(payload: dict[str, Any]) -> tuple[int, str]:
        calls.append(payload.get("amount", 0))
        return 201, json.dumps({"charged": payload.get("amount", 0)})

    layer.handler = handler  # type: ignore[attr-defined]
    return layer, clock, calls


def call(layer: IdempotencyLayer, key: str, payload: dict[str, Any], calls: list[int]) -> Result:
    def handler(body: dict[str, Any]) -> tuple[int, str]:
        calls.append(body.get("amount", 0))
        return 201, json.dumps({"charged": body.get("amount", 0)})

    return layer.run(key, payload, handler)


class TestFingerprint(unittest.TestCase):
    def test_key_order_does_not_matter(self) -> None:
        self.assertEqual(
            IdempotencyLayer.fingerprint({"a": 1, "b": [2, 3]}),
            IdempotencyLayer.fingerprint({"b": [2, 3], "a": 1}),
        )

    def test_different_payloads_differ(self) -> None:
        self.assertNotEqual(
            IdempotencyLayer.fingerprint({"amount": 10}),
            IdempotencyLayer.fingerprint({"amount": 11}),
        )

    def test_it_is_short_and_hex(self) -> None:
        value = IdempotencyLayer.fingerprint({"a": 1})
        self.assertEqual(len(value), 32)
        self.assertTrue(all(character in "0123456789abcdef" for character in value))

    def test_unserialisable_payload_is_an_error(self) -> None:
        with self.assertRaises(IdempotencyError):
            IdempotencyLayer.fingerprint({"when": object()})


class TestKeyValidation(unittest.TestCase):
    def test_keys_are_validated_before_anything_runs(self) -> None:
        layer, _, calls = make()
        for bad in ("", "   ", None, 7, "x" * 256):
            with self.assertRaises(IdempotencyError):
                layer.run(bad, {"amount": 1}, lambda payload: (200, ""))  # type: ignore[arg-type]
        self.assertEqual(calls, [])

    def test_ttl_must_be_positive(self) -> None:
        with self.assertRaises(IdempotencyError):
            IdempotencyLayer(MemoryStore(FakeClock()), FakeClock(), ttl_ms=0)


class TestReplay(unittest.TestCase):
    def test_the_effect_runs_once_and_replays_after(self) -> None:
        layer, _, calls = make(ttl_ms=1000)
        first = call(layer, "k1", {"amount": 10}, calls)
        second = call(layer, "k1", {"amount": 10}, calls)
        self.assertEqual((first.replayed, second.replayed), (False, True))
        self.assertEqual(first.body, second.body)
        self.assertEqual(first.status, second.status)
        self.assertEqual(calls, [10])

    def test_stats_count_executions_and_replays(self) -> None:
        layer, _, calls = make()
        for _ in range(3):
            call(layer, "k1", {"amount": 1}, calls)
        self.assertEqual(layer.stats["executed"], 1)
        self.assertEqual(layer.stats["replayed"], 2)
        self.assertEqual(len(layer.store), 1)

    def test_a_replay_of_a_json_body_is_byte_identical(self) -> None:
        layer, _, calls = make()
        first = call(layer, "k1", {"amount": 10}, calls)
        second = call(layer, "k1", {"amount": 10}, calls)
        self.assertEqual(json.loads(first.body), json.loads(second.body))
        self.assertTrue(second.replayed)

    def test_the_result_has_a_stable_dict_form(self) -> None:
        layer, _, calls = make()
        payload = call(layer, "k1", {"amount": 1}, calls).as_dict()
        self.assertEqual(sorted(payload), ["body", "replayed", "status"])


class TestConflicts(unittest.TestCase):
    def test_a_different_payload_conflicts_and_runs_nothing(self) -> None:
        layer, _, calls = make()
        call(layer, "k1", {"amount": 10}, calls)
        with self.assertRaises(IdempotencyConflict) as caught:
            call(layer, "k1", {"amount": 99}, calls)
        self.assertIn("different payload", str(caught.exception))
        self.assertEqual(calls, [10])
        self.assertEqual(layer.stats["conflicts"], 1)

    def test_key_order_is_not_a_conflict(self) -> None:
        layer, _, calls = make()
        call(layer, "k1", {"a": 1, "b": 2}, calls)
        replay = call(layer, "k1", {"b": 2, "a": 1}, calls)
        self.assertTrue(replay.replayed)

    def test_a_different_key_is_independent(self) -> None:
        layer, _, calls = make()
        call(layer, "k1", {"amount": 10}, calls)
        call(layer, "k2", {"amount": 10}, calls)
        self.assertEqual(calls, [10, 10])


class TestFailureDoesNotPoison(unittest.TestCase):
    def test_a_raising_handler_stores_nothing(self) -> None:
        layer, _, calls = make()

        def broken(payload: dict[str, Any]) -> tuple[int, str]:
            raise RuntimeError("primary is down")

        with self.assertRaises(RuntimeError):
            layer.run("k1", {"amount": 5}, broken)
        self.assertEqual(len(layer.store), 0)
        self.assertEqual(layer.stats["executed"], 0)

    def test_the_retry_executes(self) -> None:
        layer, _, calls = make()

        def broken(payload: dict[str, Any]) -> tuple[int, str]:
            raise RuntimeError("once")

        with self.assertRaises(RuntimeError):
            layer.run("k1", {"amount": 5}, broken)
        result = call(layer, "k1", {"amount": 5}, calls)
        self.assertFalse(result.replayed)
        self.assertEqual(calls, [5])

    def test_a_bad_handler_return_value_is_an_error(self) -> None:
        layer, _, _ = make()
        with self.assertRaises(IdempotencyError):
            layer.run("k1", {}, lambda payload: (200, "", "extra"))  # type: ignore[arg-type]
        with self.assertRaises(IdempotencyError):
            layer.run("k2", {}, lambda payload: (999, ""))
        self.assertEqual(len(layer.store), 0)


class TestExpiry(unittest.TestCase):
    def test_a_record_expires_and_the_effect_runs_again(self) -> None:
        layer, clock, calls = make(ttl_ms=1000)
        call(layer, "k1", {"amount": 10}, calls)
        clock.advance(999)
        self.assertTrue(call(layer, "k1", {"amount": 10}, calls).replayed)
        clock.advance(1)
        self.assertFalse(call(layer, "k1", {"amount": 10}, calls).replayed)
        self.assertEqual(calls, [10, 10])

    def test_an_expired_key_may_be_reused_with_a_different_payload(self) -> None:
        layer, clock, calls = make(ttl_ms=100)
        call(layer, "k1", {"amount": 10}, calls)
        clock.advance(100)
        result = call(layer, "k1", {"amount": 42}, calls)
        self.assertFalse(result.replayed)
        self.assertEqual(calls, [10, 42])

    def test_purge_drops_only_expired_records(self) -> None:
        clock = FakeClock(0)
        store = MemoryStore(clock)
        store.put("old", Record("f", 200, "a", 0, 10))
        store.put("new", Record("f", 200, "b", 0, 10_000))
        clock.advance(10)
        self.assertEqual(store.purge(), 1)
        self.assertEqual(len(store), 1)
        self.assertIn("new", store)


class TestStoreInterface(unittest.TestCase):
    def test_get_put_delete_len(self) -> None:
        store = MemoryStore(FakeClock(0))
        record = Record("f", 200, "ok", 0, 100)
        self.assertIsNone(store.get("k"))
        store.put("k", record)
        self.assertEqual(store.get("k"), record)
        self.assertTrue(store.delete("k"))
        self.assertFalse(store.delete("k"))
        self.assertEqual(len(store), 0)

    def test_an_expired_record_is_not_returned(self) -> None:
        clock = FakeClock(0)
        store = MemoryStore(clock)
        store.put("k", Record("f", 200, "ok", 0, 5))
        clock.advance(5)
        self.assertIsNone(store.get("k"))
        self.assertEqual(len(store), 0)

    def test_a_custom_store_can_be_plugged_in(self) -> None:
        class CountingStore:
            def __init__(self) -> None:
                self.rows: dict[str, Record] = {}
                self.reads = 0

            def get(self, key: str) -> Record | None:
                self.reads += 1
                return self.rows.get(key)

            def put(self, key: str, record: Record) -> None:
                self.rows[key] = record

            def delete(self, key: str) -> bool:
                return self.rows.pop(key, None) is not None

        clock = FakeClock(0)
        store = CountingStore()
        layer = IdempotencyLayer(store, clock, ttl_ms=1000)
        calls: list[int] = []
        call(layer, "k1", {"amount": 1}, calls)
        call(layer, "k1", {"amount": 1}, calls)
        self.assertEqual(calls, [1])
        self.assertEqual(store.reads, 2)
        self.assertEqual(len(store.rows), 1)


class TestInFlight(unittest.TestCase):
    def test_a_reentrant_call_is_refused(self) -> None:
        layer, _, _ = make()
        seen: list[str] = []

        def handler(payload: Any) -> tuple[int, str]:
            try:
                layer.run("k1", payload, handler)
            except RequestInFlight:
                seen.append("in-flight")
            return 200, "{}"

        layer.run("k1", {"amount": 1}, handler)
        self.assertEqual(seen, ["in-flight"])
        self.assertEqual(layer.stats["rejected"], 1)

    def test_the_in_flight_set_is_cleared_after_a_failure(self) -> None:
        layer, _, calls = make()

        def broken(payload: Any) -> tuple[int, str]:
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            layer.run("k1", {"amount": 1}, broken)
        self.assertEqual(layer.run("k1", {"amount": 1}, lambda payload: (200, "ok")).status, 200)

    def test_eight_threads_run_the_effect_once(self) -> None:
        clock = FakeClock(0)
        layer = IdempotencyLayer(MemoryStore(clock), clock, ttl_ms=1000)
        executed: list[str] = []
        barrier = threading.Barrier(8)
        outcomes: list[str] = []

        def handler(payload: Any) -> tuple[int, str]:
            executed.append("ran")
            return 200, "done"

        def worker() -> None:
            barrier.wait()
            try:
                result = layer.run("k1", {"amount": 1}, handler)
                outcomes.append("replayed" if result.replayed else "executed")
            except RequestInFlight:
                outcomes.append("in-flight")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(executed), 1, executed)
        self.assertEqual(len(outcomes), 8)
        self.assertEqual(outcomes.count("executed"), 1)
        self.assertEqual(outcomes.count("replayed") + outcomes.count("in-flight"), 7)


class TestCli(unittest.TestCase):
    def test_replays_after_the_first_execution(self) -> None:
        code, out, err = run_cli("--key", "charge-1", "--payload", '{"amount": 10}', "--calls", "3")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("executed"), 1)
        self.assertEqual(out.count("replayed"), 2)
        self.assertIn("executions: 1", err)

    def test_fail_once_then_execute(self) -> None:
        code, out, err = run_cli("--key", "charge-2", "--fail-once", "--calls", "2")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("failed"), 1)
        self.assertEqual(out.count("executed"), 1)
        self.assertIn("downstream timeout", out)

    def test_json_output_has_one_row_per_call(self) -> None:
        code, out, _ = run_cli("--key", "charge-3", "--calls", "2", "--json")
        self.assertEqual(code, 0)
        rows = [json.loads(line) for line in out.strip().splitlines()]
        self.assertEqual([row["outcome"] for row in rows], ["executed", "replayed"])
        self.assertEqual(rows[0]["status"], 201)

    def test_bad_input_exits_two(self) -> None:
        code, _, err = run_cli("--key", "k", "--payload", "{oops")
        self.assertEqual(code, 2)
        self.assertIn("not JSON", err)
        code, _, err = run_cli("--key", "k", "--calls", "0")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
