"""Tests for Longest Substring Without Repeating Characters."""
from __future__ import annotations

import random
import unittest

from longest_substring import length_of_longest_substring


def brute_force(s: str) -> int:
    """Check every substring — O(n^3) reference for small inputs."""
    best = 0
    for i in range(len(s)):
        for j in range(i, len(s)):
            window = s[i : j + 1]
            if len(set(window)) == len(window):
                best = max(best, len(window))
    return best


class TestLongestSubstring(unittest.TestCase):
    def test_leetcode_examples(self) -> None:
        self.assertEqual(length_of_longest_substring("abcabcbb"), 3)
        self.assertEqual(length_of_longest_substring("bbbbb"), 1)
        self.assertEqual(length_of_longest_substring("pwwkew"), 3)

    def test_empty_and_single(self) -> None:
        self.assertEqual(length_of_longest_substring(""), 0)
        self.assertEqual(length_of_longest_substring("a"), 1)

    def test_repeat_after_window_was_dropped(self) -> None:
        # The classic trap: don't move `left` backwards for dropped characters.
        self.assertEqual(length_of_longest_substring("dvdf"), 3)
        self.assertEqual(length_of_longest_substring("abba"), 2)

    def test_spaces_and_symbols_count_as_characters(self) -> None:
        self.assertEqual(length_of_longest_substring(" "), 1)
        self.assertEqual(length_of_longest_substring("a b"), 3)
        self.assertEqual(length_of_longest_substring("a1!a1!"), 3)

    def test_whole_string_is_unique(self) -> None:
        self.assertEqual(length_of_longest_substring("abcdefg"), 7)

    def test_against_brute_force_random(self) -> None:
        rng = random.Random(20261120)
        for _ in range(300):
            s = "".join(rng.choice("abcde") for _ in range(rng.randint(0, 9)))
            self.assertEqual(length_of_longest_substring(s), brute_force(s), s)


if __name__ == "__main__":
    unittest.main()
