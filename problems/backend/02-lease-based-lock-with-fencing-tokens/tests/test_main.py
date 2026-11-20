"""Tests for lease expiry, fencing tokens, the guarded resource, and races."""
from __future__ import annotations

import io
import json
import threading
import unittest
from contextlib import redirect_stdout

from solution import cli
from solution.main import (
    FakeClock,
    FenceGuard,
    FenceToken,
    LeaseLock,
    LockHeld,
    StaleTokenError,
)


def make_lock(lease: float = 10.0) -> tuple[FakeClock, LeaseLock]:
    clock = FakeClock()
    return clock, LeaseLock(clock, lease=lease, name="invoice-42")


class TestClock(unittest.TestCase):
    def test_advance_is_additive_and_rejects_time_travel(self) -> None:
        clock = FakeClock(start=5.0)
        self.assertEqual(clock.advance(2.5), 7.5)
        self.assertEqual(clock.now(), 7.5)
        with self.assertRaises(ValueError):
            clock.advance(-1.0)

    def test_construction_validates_input(self) -> None:
        with self.assertRaises(ValueError):
            LeaseLock(FakeClock(), lease=0)
        with self.assertRaises(ValueError):
            LeaseLock(FakeClock(), lease=-3)


class TestAcquire(unittest.TestCase):
    def test_first_grant_carries_fence_one_and_the_lease_window(self) -> None:
        clock, lock = make_lock(lease=10.0)
        token = lock.acquire("worker-a")
        self.assertEqual((token.owner, token.fence, token.expires_at), ("worker-a", 1, 10.0))
        self.assertEqual(token.remaining(clock), 10.0)

    def test_second_acquire_while_live_raises_with_the_holder(self) -> None:
        _, lock = make_lock()
        holder = lock.acquire("worker-a")
        with self.assertRaises(LockHeld) as raised:
            lock.acquire("worker-b")
        self.assertIs(raised.exception.holder, holder)
        self.assertIn("worker-a", str(raised.exception))

    def test_try_acquire_is_the_exception_free_form(self) -> None:
        _, lock = make_lock()
        self.assertIsNotNone(lock.try_acquire("worker-a"))
        self.assertIsNone(lock.try_acquire("worker-b"))

    def test_owner_must_be_a_non_empty_string(self) -> None:
        _, lock = make_lock()
        with self.assertRaises(ValueError):
            lock.acquire("")

    def test_expiry_boundary_is_exclusive(self) -> None:
        clock, lock = make_lock(lease=10.0)
        token = lock.acquire("worker-a")
        clock.advance(9.999)
        self.assertFalse(token.is_expired(clock))
        clock.advance(0.001)
        self.assertTrue(token.is_expired(clock), "a lease never covers its final instant")
        self.assertIsNone(lock.holder)

    def test_takeover_after_expiry_gets_the_next_fence(self) -> None:
        clock, lock = make_lock(lease=10.0)
        first = lock.acquire("worker-a")
        clock.advance(10.0)
        second = lock.acquire("worker-b")
        self.assertEqual(second.fence, first.fence + 1)
        self.assertEqual(lock.fence, 2)

    def test_fence_never_repeats_even_after_release(self) -> None:
        _, lock = make_lock()
        fences = []
        for _ in range(5):
            token = lock.acquire("worker")
            fences.append(token.fence)
            self.assertTrue(lock.release("worker", token))
        self.assertEqual(fences, [1, 2, 3, 4, 5])


class TestRenewAndRelease(unittest.TestCase):
    def test_renew_moves_the_deadline_from_now(self) -> None:
        clock, lock = make_lock(lease=10.0)
        token = lock.acquire("worker-a")
        clock.advance(8.0)
        self.assertTrue(lock.renew("worker-a", token))
        renewed = lock.holder
        assert renewed is not None
        self.assertEqual(renewed.expires_at, 18.0)
        clock.advance(8.0)  # 16.0: past the first deadline, inside the second
        self.assertEqual(lock.holder.owner, "worker-a")

    def test_renew_after_expiry_fails(self) -> None:
        clock, lock = make_lock(lease=10.0)
        token = lock.acquire("worker-a")
        clock.advance(10.0)
        self.assertFalse(lock.renew("worker-a", token))

    def test_renew_requires_the_current_token_and_owner(self) -> None:
        clock, lock = make_lock(lease=10.0)
        token = lock.acquire("worker-a")
        foreign = FenceToken("worker-b", token.fence, token.expires_at)
        self.assertFalse(lock.renew("worker-b", foreign))
        self.assertFalse(lock.renew("worker-b", token), "owner string must match too")
        self.assertTrue(lock.renew("worker-a", token))

    def test_renew_of_a_superseded_token_does_not_steal_the_lease(self) -> None:
        clock, lock = make_lock(lease=10.0)
        first = lock.acquire("worker-a")
        clock.advance(10.0)
        second = lock.acquire("worker-b")
        clock.advance(1.0)
        self.assertFalse(lock.renew("worker-a", first))
        self.assertTrue(lock.renew("worker-b", second))
        self.assertEqual(lock.holder.owner, "worker-b")

    def test_release_frees_the_lock_once(self) -> None:
        _, lock = make_lock()
        token = lock.acquire("worker-a")
        self.assertTrue(lock.release("worker-a", token))
        self.assertIsNone(lock.holder)
        self.assertFalse(lock.release("worker-a", token), "the same token cannot free twice")

    def test_stale_release_does_not_disturb_the_new_holder(self) -> None:
        clock, lock = make_lock(lease=10.0)
        first = lock.acquire("worker-a")
        clock.advance(10.0)
        lock.acquire("worker-b")
        self.assertFalse(lock.release("worker-a", first))
        self.assertEqual(lock.holder.owner, "worker-b")

    def test_snapshot_reports_state(self) -> None:
        clock, lock = make_lock(lease=10.0)
        lock.acquire("worker-a")
        clock.advance(2.0)
        snapshot = lock.snapshot()
        self.assertEqual(snapshot["name"], "invoice-42")
        self.assertEqual(snapshot["holder"], "worker-a")
        self.assertEqual((snapshot["fence"], snapshot["grants"], snapshot["now"]), (1, 1, 2.0))
        lock.release("worker-a", lock.holder)
        self.assertIsNone(lock.snapshot()["holder"])


class TestFenceGuard(unittest.TestCase):
    def test_writes_are_applied_in_increasing_fence_order(self) -> None:
        guard = FenceGuard()
        self.assertEqual(guard.write(FenceToken("a", 1, 0.0), "v1"), 1)
        self.assertEqual(guard.write(FenceToken("b", 2, 0.0), "v2"), 2)
        self.assertEqual([fence for fence, _ in guard.writes], [1, 2])
        self.assertEqual(guard.value, "v2")
        self.assertEqual(guard.highest, 2)

    def test_duplicate_and_stale_fences_are_rejected(self) -> None:
        guard = FenceGuard()
        guard.write(FenceToken("a", 3, 0.0), "v3")
        with self.assertRaises(StaleTokenError) as raised:
            guard.write(FenceToken("a", 3, 0.0), "again")
        self.assertEqual((raised.exception.fence, raised.exception.highest), (3, 3))
        self.assertFalse(guard.try_write(FenceToken("a", 2, 0.0), "older"))
        self.assertEqual(guard.value, "v3")
        self.assertEqual(guard.highest, 3)

    def test_empty_guard_has_no_value(self) -> None:
        self.assertIsNone(FenceGuard().value)
        self.assertEqual(FenceGuard().writes, ())

    def test_concurrent_writers_are_serialised_and_ordered(self) -> None:
        guard = FenceGuard()
        tokens = [FenceToken(f"w{index}", index, 0.0) for index in range(1, 41)]
        threads = [
            threading.Thread(target=guard.try_write, args=(token, f"value {token.fence}"))
            for token in tokens
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        applied = [fence for fence, _ in guard.writes]
        self.assertEqual(applied, sorted(applied), "the guard must apply fences in order")
        self.assertEqual(guard.highest, 40)
        self.assertEqual(set(applied) | {fence for fence in range(1, 41) if fence not in applied}, set(range(1, 41)))
        self.assertGreaterEqual(len(applied), 1, "at least the highest fence gets through")


class TestRaceAndStory(unittest.TestCase):
    def test_stalled_worker_cannot_apply_after_takeover(self) -> None:
        clock, lock = make_lock(lease=10.0)
        guard = FenceGuard()

        stalled = lock.acquire("worker-a")
        guard.write(stalled, "v1 by worker-a")

        clock.advance(15.0)  # worker-a is frozen
        fresh = lock.acquire("worker-b")
        guard.write(fresh, "v2 by worker-b")

        with self.assertRaises(StaleTokenError):
            guard.write(stalled, "v1 by worker-a (late)")
        self.assertEqual(guard.value, "v2 by worker-b")
        self.assertEqual(guard.highest, fresh.fence)

    def test_renewing_holder_never_loses_the_lease(self) -> None:
        clock, lock = make_lock(lease=10.0)
        token = lock.acquire("worker-a")
        for _ in range(10):
            clock.advance(9.0)
            self.assertTrue(lock.renew("worker-a", token))
            self.assertIsNone(lock.try_acquire("worker-b"))
        self.assertEqual(lock.fence, 1, "no takeover means no new fence")

    def test_many_threads_never_produce_two_live_holders(self) -> None:
        _, lock = make_lock(lease=1000.0)
        winners: list[FenceToken] = []
        barrier = threading.Barrier(16)
        guard = threading.Lock()

        def contend(worker: int) -> None:
            barrier.wait()
            token = lock.try_acquire(f"worker-{worker}")
            if token is not None:
                with guard:
                    winners.append(token)

        threads = [threading.Thread(target=contend, args=(index,)) for index in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(winners), 1, f"expected exactly one winner, got {len(winners)}")
        self.assertEqual(winners[0].fence, 1)

    def test_sequential_contenders_see_a_rising_fence(self) -> None:
        clock, lock = make_lock(lease=1.0)
        fences = []
        for _ in range(4):
            token = lock.acquire("worker")
            fences.append(token.fence)
            clock.advance(1.0)
        self.assertEqual(fences, [1, 2, 3, 4])


class TestCli(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        return code, buffer.getvalue()

    def test_stalled_writer_story(self) -> None:
        code, output = self.run_cli(["--lease", "10", "--stall", "15"])
        self.assertEqual(code, 0)
        self.assertIn("worker-a's late write rejected: True", output)
        self.assertIn("applied fences: [1, 2]", output)

    def test_live_lease_keeps_the_second_worker_out(self) -> None:
        code, output = self.run_cli(["--lease", "10", "--stall", "3"])
        self.assertEqual(code, 1)
        self.assertIn("could not acquire", output)
        self.assertIn("applied fences: [1]", output)

    def test_json_report_is_machine_readable(self) -> None:
        code, output = self.run_cli(["--lease", "5", "--stall", "6", "--json"])
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertTrue(report["stale_rejected"])
        self.assertEqual(report["second"]["fence"], 2)
        self.assertEqual(report["value"], "v2 by worker-b")

    def test_bad_arguments_exit_two(self) -> None:
        self.assertEqual(self.run_cli(["--lease", "0"])[0], 2)
        self.assertEqual(self.run_cli(["--stall", "-1"])[0], 2)


if __name__ == "__main__":
    unittest.main()
