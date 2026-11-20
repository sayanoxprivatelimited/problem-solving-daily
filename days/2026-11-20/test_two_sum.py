"""Tests for Two Sum — examples, edge cases and a brute-force cross-check."""
from __future__ import annotations

import random
import unittest

from two_sum import two_sum


def brute_force(nums: list[int], target: int) -> tuple[int, int] | None:
    """O(n^2) reference implementation used to validate the fast one."""
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] + nums[j] == target:
                return i, j
    return None


class TestTwoSum(unittest.TestCase):
    def test_leetcode_examples(self) -> None:
        self.assertEqual(two_sum([2, 7, 11, 15], 9), (0, 1))
        self.assertEqual(two_sum([3, 2, 4], 6), (1, 2))
        self.assertEqual(two_sum([3, 3], 6), (0, 1))

    def test_negatives(self) -> None:
        self.assertEqual(two_sum([-3, 4, 3, 90], 0), (0, 2))
        self.assertEqual(two_sum([-1, -2, -3, -4, -5], -8), (2, 4))

    def test_zero_target_with_zeroes(self) -> None:
        self.assertEqual(two_sum([0, 4, 3, 0], 0), (0, 3))

    def test_minimum_length(self) -> None:
        self.assertEqual(two_sum([1, 1], 2), (0, 1))

    def test_answer_reported_is_valid(self) -> None:
        # Order of the pair is not part of the contract, validity is.
        nums, target = [5, 1, 9, 4], 10
        i, j = two_sum(nums, target)
        self.assertEqual(nums[i] + nums[j], target)
        self.assertNotEqual(i, j)

    def test_raises_when_no_answer(self) -> None:
        with self.assertRaises(ValueError):
            two_sum([1, 2, 3], 100)
        with self.assertRaises(ValueError):
            two_sum([], 5)

    def test_against_brute_force_random(self) -> None:
        rng = random.Random(20261120)
        for _ in range(300):
            size = rng.randint(2, 12)
            nums = [rng.randint(-20, 20) for _ in range(size)]
            i = rng.randrange(size)
            j = rng.randrange(size)
            if i == j:  # guarantee a solvable pair by construction
                j = (i + 1) % size
            target = nums[i] + nums[j]

            expected = brute_force(nums, target)
            got_i, got_j = two_sum(nums, target)
            self.assertEqual(nums[got_i] + nums[got_j], target)
            self.assertNotEqual(got_i, got_j)
            self.assertIsNotNone(expected)


if __name__ == "__main__":
    unittest.main()
