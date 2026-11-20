"""LeetCode 3 — Longest Substring Without Repeating Characters (Medium).

Problem
-------
Given a string `s`, return the length of the longest substring that contains no
repeated character.

Examples
--------
>>> length_of_longest_substring("abcabcbb")
3
>>> length_of_longest_substring("bbbbb")
1
>>> length_of_longest_substring("pwwkew")
3
>>> length_of_longest_substring("")
0
>>> length_of_longest_substring("dvdf")
3

Approach — sliding window with a "last seen" map
------------------------------------------------
Keep a window `[left, right]` that is always duplicate-free.  Extend `right` one
character at a time and remember `last_seen[ch] = right`.

If `ch` was seen *inside the current window*, the new window can only start one
position after that copy, so `left = last_seen[ch] + 1`.  The check
`last_seen[ch] >= left` matters: a character seen only to the *left* of the
window (already dropped) must **not** move the window again — that is exactly the
bug that makes `"dvdf"` return 2 instead of 3.

Window length after each step is `right - left + 1`; keep the maximum.

Complexity
----------
Time  : O(n) — each character enters and leaves the window at most once.
Space : O(min(n, alphabet)) — at most one entry per distinct character.

Edge cases
----------
* Empty string -> 0 (loop never runs).
* All identical characters -> 1.
* The dropped-character case ("dvdf", "abba") is the classic trap, see tests.
"""
from __future__ import annotations


def length_of_longest_substring(s: str) -> int:
    """Length of the longest substring of `s` with no repeated character."""
    last_seen: dict[str, int] = {}
    left = 0
    best = 0
    for right, ch in enumerate(s):
        if ch in last_seen and last_seen[ch] >= left:
            left = last_seen[ch] + 1
        last_seen[ch] = right
        best = max(best, right - left + 1)
    return best
