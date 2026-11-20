"""Tests for Coin Change — examples, greedy trap and a brute-force cross-check."""
from __future__ import annotations

import random
import unittest

from coin_change import coin_change


def brute_force(coins: list[int], amount: int) -> int:
    """Exhaustive search over every multiset of coins — small inputs only."""
    best = [float("inf")]

    def explore(remaining: int, used: int) -> None:
        if remaining == 0:
            best[0] = min(best[0], used)
            return
        if remaining < 0 or used >= best[0]:
            return
        for coin in coins:
            explore(remaining - coin, used + 1)

    explore(amount, 0)
    return -1 if best[0] == float("inf") else int(best[0])


class TestCoinChange(unittest.TestCase):
    def test_leetcode_examples(self) -> None:
        self.assertEqual(coin_change([1, 2, 5], 11), 3)
        self.assertEqual(coin_change([2], 3), -1)

    def test_zero_amount(self) -> None:
        self.assertEqual(coin_change([1], 0), 0)
        self.assertEqual(coin_change([7, 9], 0), 0)

    def test_impossible_amounts(self) -> None:
        self.assertEqual(coin_change([5], 3), -1)
        self.assertEqual(coin_change([3, 7], 5), -1)
        self.assertEqual(coin_change([], 7), -1)

    def test_greedy_would_fail(self) -> None:
        # 4 + 1 + 1 = 3 coins greedily; 3 + 3 = 2 coins is optimal.
        self.assertEqual(coin_change([1, 3, 4], 6), 2)

    def test_large_coin_is_skipped(self) -> None:
        self.assertEqual(coin_change([1, 100], 50), 50)

    def test_negative_amount_raises(self) -> None:
        with self.assertRaises(ValueError):
            coin_change([1], -1)

    def test_against_brute_force_random(self) -> None:
        rng = random.Random(20261120)
        for _ in range(120):
            coins = rng.sample(range(1, 9), rng.randint(1, 4))
            amount = rng.randint(0, 22)
            self.assertEqual(
                coin_change(coins, amount), brute_force(coins, amount), (coins, amount)
            )


if __name__ == "__main__":
    unittest.main()
