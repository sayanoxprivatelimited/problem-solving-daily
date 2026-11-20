"""LeetCode 322 — Coin Change (Medium).

Problem
-------
Given coin denominations `coins` and an `amount`, return the fewest coins needed
to make up exactly `amount`.  If the amount cannot be formed, return -1.  You
have an unlimited supply of each coin.

Examples
--------
>>> coin_change([1, 2, 5], 11)
3
>>> coin_change([2], 3)
-1
>>> coin_change([1], 0)
0
>>> coin_change([5], 3)
-1

Approach — bottom-up 1D DP
--------------------------
`dp[v]` = fewest coins that sum to exactly `v`.  Base: `dp[0] = 0` (zero coins).
For every value `1..amount` try each coin `c <= v` and take the best of
`dp[v - c] + 1`.  Because `dp[v - c]` was solved for a strictly smaller value,
filling the table upwards is enough — no recursion, no memo dict.

Unreachable amounts must stay distinguishable from "0 coins", so we seed the
table with `INF = amount + 1` (any real answer uses at most `amount` coins of
value 1).  Answer is -1 iff `dp[amount]` is still larger than `amount`.

Greedy does **not** work here: `coins=[1, 3, 4], amount=6` gives 4+1+1 = 3 coins
greedily, while 3+3 = 2 coins is optimal.  Try it with the tests.

Complexity
----------
Time  : O(amount * len(coins)) — nested loops over values and coins.
Space : O(amount) — one row of the DP table.

Edge cases
----------
* `amount = 0` -> 0 (no coins needed).
* Coin larger than the amount is skipped by the `coin <= value` guard.
* Impossible amounts -> -1.
"""
from __future__ import annotations

from typing import Sequence


def coin_change(coins: Sequence[int], amount: int) -> int:
    """Fewest coins summing to exactly `amount`, or -1 if impossible."""
    if amount < 0:
        raise ValueError("amount must be non-negative")

    infinity = amount + 1
    dp = [0] + [infinity] * amount

    for value in range(1, amount + 1):
        for coin in coins:
            if coin <= value and dp[value - coin] + 1 < dp[value]:
                dp[value] = dp[value - coin] + 1

    return -1 if dp[amount] > amount else dp[amount]
