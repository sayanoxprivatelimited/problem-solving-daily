---
title: Unified diff and patch engine
category: coding
level: hard
concepts: diff, Myers algorithm, hunks, patch application, fuzz testing
status: solved
---

# Unified diff and patch engine

## Context

Code review, deployments and three-way merges all rest on one primitive: given two
versions of a text file, produce the **shortest sensible description of the change**,
in the format every tool already speaks — a unified diff.

Two halves, and both are easy to get subtly wrong:

* **Producing** a diff means a real edit script. The naive algorithm (longest common
  subsequence by dynamic programming) is O(n·m) and collapses on a 5,000-line file.
  Myers' O(ND) algorithm walks diagonals instead, and its output must then be grouped
  into hunks with exactly the right context.
* **Applying** a patch means finding where a hunk belongs in a file that may have
  drifted since the diff was made, refusing a hunk that does not match, and treating
  a missing trailing newline as a real, visible fact (`\ No newline at end of file`)
  rather than as whitespace trivia.

The property that catches everything: `apply_patch(a, diff(a, b)) == b` for **every**
pair of inputs. That is what the fuzz test proves.

## Input / Output

```
diff(a, b, context=3, from_name, to_name) -> unified diff text ("" when a == b)
apply_patch(a, patch)                     -> the patched text
errors                                    -> PatchError (no hunk applies / malformed patch)
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | The edit script is minimal: Myers' shortest edit script, not an LCS table | A 5,000-line file must not cost 25 million comparisons |
| 2 | Hunks carry `context` unchanged lines on each side, and merge when their context would overlap | That is what makes a patch reviewable and applicable |
| 3 | A last line without a newline is marked with `\ No newline at end of file` on both sides | Silently "fixing" the newline corrupts files |
| 4 | `apply_patch` tolerates a shifted hunk (line numbers drifted) by searching, but only applies an **exact** match | Fuzzy matching silently patches the wrong place |
| 5 | A hunk that cannot be found raises `PatchError` with the hunk's line number | A patch that half-applies is worse than one that fails |
| 6 | Empty files, single-line files and pure insertions are representable | `@@ -0,0 +1,3 @@` is a real header |

## Constraints & acceptance criteria

- Standard library only; no `difflib` (the point is to implement the algorithm).
- `diff` is deterministic: the same inputs always produce byte-identical output.
- `apply_patch` never mutates its input and never writes a partial result.
- Round-trip property: `apply_patch(old, diff(old, new)) == new`, fuzzed over
  random line soups including empty strings and missing trailing newlines.
- Documented limit: Myers is O(ND) in the number of edits and memory is O(D²) for the
  trace; files above ~100k lines need a different strategy (patience diff, or
  streaming).

## Assumptions (write them down — real problems are under-specified)

- Text is UTF-8 and split with `str.splitlines(keepends=True)`, so `\r\n` endings are
  preserved as part of the line.
- A "line" is whatever `splitlines` returns; a lone `\r` is a line boundary.
- Patches are unified diffs with 3 lines of context unless the caller asks otherwise.
- Application is in order and uses one drift offset accumulated across hunks, like
  GNU `patch` does.

## How to run

```bash
cd problems/coding/02-unified-diff-and-patch-engine
python -m unittest discover -s tests -t .
python -m solution.cli diff old.txt new.txt
python -m solution.cli apply change.patch old.txt -o new.txt
```

## Stretch goals

- Interdiff / three-way merge on top of the same edit script.
- Patience or histogram diff for the "huge file, few changes" case.
- Whitespace-insensitive application (`-l`), and a `--dry-run` that reports every hunk
  it would touch.
