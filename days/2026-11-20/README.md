# Day 1 — 2026-11-20

**Theme:** Hash maps & sliding window, first contact with bottom-up DP
**Time spent:** ~45 minutes · **Solved:** 3 / 3

| # | Problem | Difficulty | Topic | Approach | Time | Space |
|---|---------|-----------|-------|----------|------|-------|
| 1 | [Two Sum](https://leetcode.com/problems/two-sum/) | 🟢 Easy | Hash map | value → index map, single pass | O(n) | O(n) |
| 2 | [Longest Substring Without Repeating Characters](https://leetcode.com/problems/longest-substring-without-repeating-characters/) | 🟡 Medium | Sliding window | window + `last_seen` map | O(n) | O(min(n, Σ)) |
| 3 | [Coin Change](https://leetcode.com/problems/coin-change/) | 🟡 Medium | DP (1D) | `dp[v]` = min coins for value `v` | O(n·amount) | O(amount) |

Each solution file is self-contained: the problem statement, the reasoning, the
complexity and runnable `>>>` examples live in its docstring. Tests sit next to
it as `test_<name>.py` and cross-check the fast solution against a brute force on
seeded random inputs.

```bash
python scripts/run_tests.py        # from the repo root
```

## What clicked today

- **Hash map = trading memory for time.** Two Sum only becomes O(n) the moment you
  realise you can *look up* `target - x` instead of scanning for it.
- **A window needs an invariant.** "No duplicates inside `[left, right]`" is the
  whole algorithm; `left` is repaired, never guessed.
- **`dp` needs a sentinel, not `0`.** `INF = amount + 1` is what makes "0 coins"
  and "impossible" different answers in Coin Change.

## What I got stuck on

- `"dvdf"` returned 2 on my first sliding-window attempt: I moved `left`
  backwards for a character that had already left the window. The fix is the
  `last_seen[ch] >= left` guard, and `"abba"` is the second test that catches it.
- Greedy looked fine for Coin Change until `coins=[1, 3, 4], amount=6`
  (greedy 4+1+1 = 3 coins, optimal 3+3 = 2 coins).

## Pattern to remember

> **Before writing a loop, ask what the loop is *looking for*.**
> If it re-scans, a dict can probably remember it for you.

## Next up

- [ ] Coin Change II (counting combinations — loop order flips)
- [ ] Longest Repeating Character Replacement (window + allowance budget)
- [ ] Container With Most Water (two pointers)
