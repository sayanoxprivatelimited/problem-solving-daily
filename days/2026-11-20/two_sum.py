"""LeetCode 1 — Two Sum (Easy).

Problem
-------
Given an array of integers `nums` and an integer `target`, return the indices of
the two numbers that add up to `target`.  Exactly one answer exists, and the
same element may not be used twice.  The pair may be returned in any order.

Examples
--------
>>> two_sum([2, 7, 11, 15], 9)
(0, 1)
>>> two_sum([3, 2, 4], 6)
(1, 2)
>>> two_sum([3, 3], 6)
(0, 1)
>>> two_sum([-3, 4, 3, 90], 0)
(0, 2)

Approach — one pass with a hash map
-----------------------------------
Walk the array once and keep a dict `seen` mapping *value -> index* for every
number already visited.  For the current number `x` the partner we need is
`target - x`; if that partner is already in `seen`, we found the answer.
Storing the index only *after* the lookup is what prevents an element from
being paired with itself.

The brute force is O(n^2) because it re-scans for every element; the dict trades
O(n) memory for O(1) average lookups, so the whole scan becomes linear.

Complexity
----------
Time  : O(n) — one pass, O(1) average dictionary lookups.
Space : O(n) — the map holds at most n entries.

Edge cases
----------
* Duplicate values ([3, 3]) work: the second 3 looks up the first one's index.
* Negative numbers and negative targets need no special handling.
* Guaranteed single answer, so we raise instead of returning None if the
  contract is violated — that keeps a bad input loud instead of silent.
"""
from __future__ import annotations

from typing import Sequence


def two_sum(nums: Sequence[int], target: int) -> tuple[int, int]:
    """Return the two indices in `nums` whose values add up to `target`."""
    seen: dict[int, int] = {}
    for i, x in enumerate(nums):
        partner = target - x
        if partner in seen:
            return seen[partner], i
        seen[x] = i
    raise ValueError("no two indices add up to target")
