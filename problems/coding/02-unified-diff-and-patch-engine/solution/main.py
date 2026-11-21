r"""A unified diff generator and patch applier, both written from scratch.

The naive way to diff two files is a longest-common-subsequence table: O(n·m) time
and O(n·m) memory. Two 5,000-line files cost 25 million comparisons and a gigabyte of
integers before the first hunk is printed. Myers' algorithm walks diagonals of the
edit graph instead, and its cost is O((n+m)·D) where D is the number of edits — for a
typical review, a handful.

Producing a diff is half the job. Applying one has its own traps:

* line numbers drift, so a hunk must be *found*, not assumed — but an approximate
  match is a silent corruption, so the found window must match exactly;
* a missing trailing newline is a visible fact (``\ No newline at end of file``),
  not whitespace to normalise away;
* a patch that cannot apply in full must apply nothing at all.

The property that catches all of it:

    >>> old = "one\ntwo\nthree\n"
    >>> new = "one\ntwo\nthree\nfour\n"
    >>> print(diff(old, new), end="")
    --- a
    +++ b
    @@ -1,3 +1,4 @@
     one
     two
     three
    +four
    >>> apply_patch(old, diff(old, new)) == new
    True

Context is what makes a patch reviewable, and hunks merge when contexts overlap:

    >>> print(diff("a\nb\nc\nd\ne\nf\ng\n", "A\nb\nc\nd\ne\nf\nG\n", context=1,
    ...            from_name="v1", to_name="v2"), end="")
    --- v1
    +++ v2
    @@ -1,2 +1,2 @@
    -a
    +A
     b
    @@ -6,2 +6,2 @@
     f
    -g
    +G

A file that does not end in a newline says so, on both sides:

    >>> patch = diff("a\nb", "a\nc")
    >>> print(patch, end="")
    --- a
    +++ b
    @@ -1,2 +1,2 @@
     a
    -b
    \ No newline at end of file
    +c
    \ No newline at end of file
    >>> apply_patch("a\nb", patch) == "a\nc"
    True

Identical files produce an empty patch, which applies to anything:

    >>> diff("same\n", "same\n")
    ''
    >>> apply_patch("same\n", "")
    'same\n'

And a patch that lands on the wrong file is refused, not guessed:

    >>> try:
    ...     apply_patch("x\ny\n", diff("a\nb\n", "a\nz\n"))
    ... except PatchError as error:
    ...     print(str(error).startswith("hunk 1 does not apply"))
    True
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "Hunk",
    "PatchError",
    "apply_patch",
    "diff",
    "parse_patch",
]

NO_NEWLINE = "\\ No newline at end of file"
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    """The patch is malformed, or a hunk does not match the file it is applied to."""


# ------------------------------------------------------------------ lines -- #
def _split(text: str) -> list[str]:
    """Split keeping line endings, so a missing final newline stays visible."""
    return text.splitlines(keepends=True)


def _emit(prefix: str, line: str, out: list[str]) -> None:
    """Write one diff body line, plus the no-newline marker when it is missing."""
    if line.endswith("\n"):
        out.append(f"{prefix}{line}")
    else:
        out.append(f"{prefix}{line}\n")
        out.append(f"{NO_NEWLINE}\n")


# ------------------------------------------------------------------ myers -- #
def _trace(a: Sequence[str], b: Sequence[str]) -> list[dict[int, int]]:
    """Run Myers' forward pass, keeping the furthest x per diagonal for every D."""
    n, m = len(a), len(b)
    v: dict[int, int] = {1: 0}
    trace: list[dict[int, int]] = []

    for d in range(n + m + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1)):
                x = v.get(k + 1, 0)
            else:
                x = v.get(k - 1, 0) + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                return trace
    return trace


def _moves(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int, int, int]]:
    """Backtrack through the trace into moves ``(prev_x, prev_y, x, y)``, oldest first."""
    trace = _trace(a, b)
    x, y = len(a), len(b)
    moves: list[tuple[int, int, int, int]] = []

    for d in range(len(trace) - 1, -1, -1):
        v = trace[d]
        k = x - y
        if k == -d or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1)):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = v.get(prev_k, 0)
        prev_y = prev_x - prev_k

        while x > prev_x and y > prev_y:
            moves.append((x - 1, y - 1, x, y))
            x, y = x - 1, y - 1
        if d > 0:
            moves.append((prev_x, prev_y, x, y))
        x, y = prev_x, prev_y

    moves.reverse()
    return moves


@dataclass(frozen=True)
class _Op:
    """One line of the edit script: keep, delete or insert."""

    tag: str  # "equal" | "delete" | "insert"
    old_index: int
    new_index: int
    text: str


def _script(a: Sequence[str], b: Sequence[str]) -> list[_Op]:
    ops: list[_Op] = []
    for prev_x, prev_y, x, y in _moves(a, b):
        if x > prev_x and y > prev_y:  # diagonal: the lines are equal
            ops.append(_Op("equal", prev_x, prev_y, a[prev_x]))
        elif x > prev_x:  # vertical: a line was deleted
            ops.append(_Op("delete", prev_x, prev_y, a[prev_x]))
        else:  # horizontal: a line was inserted
            ops.append(_Op("insert", prev_x, prev_y, b[prev_y]))
    return ops


# ------------------------------------------------------------------ hunks -- #
@dataclass(frozen=True)
class Hunk:
    """One ``@@`` block: ranges on both sides plus the prefixed body lines."""

    old_start: int
    old_length: int
    new_start: int
    new_length: int
    lines: tuple[tuple[str, str], ...]  # (prefix, text) with text keeping its newline

    @property
    def old_lines(self) -> tuple[str, ...]:
        return tuple(text for prefix, text in self.lines if prefix != "+")

    @property
    def new_lines(self) -> tuple[str, ...]:
        return tuple(text for prefix, text in self.lines if prefix != "-")


def _format_range(start: int, length: int) -> str:
    return f"{start}" if length == 1 else f"{start},{length}"


def _hunks(a: Sequence[str], b: Sequence[str], context: int) -> list[Hunk]:
    ops = _script(a, b)
    changes = [index for index, op in enumerate(ops) if op.tag != "equal"]
    if not changes:
        return []

    groups: list[list[int]] = []
    for index in changes:
        if groups and index - groups[-1][-1] - 1 <= 2 * context:
            groups[-1].append(index)
        else:
            groups.append([index])

    hunks: list[Hunk] = []
    for group in groups:
        start = max(0, group[0] - context)
        end = min(len(ops) - 1, group[-1] + context)
        window = ops[start : end + 1]

        old_length = sum(1 for op in window if op.tag != "insert")
        new_length = sum(1 for op in window if op.tag != "delete")
        old_start = window[0].old_index + 1 if old_length else window[0].old_index
        new_start = window[0].new_index + 1 if new_length else window[0].new_index

        prefix = {"equal": " ", "delete": "-", "insert": "+"}
        lines = tuple((prefix[op.tag], op.text) for op in window)
        hunks.append(Hunk(old_start, old_length, new_start, new_length, lines))
    return hunks


def diff(
    old: str, new: str, context: int = 3, from_name: str = "a", to_name: str = "b"
) -> str:
    """Return the unified diff that turns ``old`` into ``new`` ("" when they match).

        >>> diff("a\\n", "a\\n")
        ''
        >>> print(diff("", "hello\\n", from_name="empty", to_name="hello"), end="")
        --- empty
        +++ hello
        @@ -0,0 +1 @@
        +hello
    """
    if context < 0:
        raise ValueError("context must be >= 0")

    a, b = _split(old), _split(new)
    hunks = _hunks(a, b, context)
    if not hunks:
        return ""

    out = [f"--- {from_name}\n", f"+++ {to_name}\n"]
    for hunk in hunks:
        out.append(
            f"@@ -{_format_range(hunk.old_start, hunk.old_length)} "
            f"+{_format_range(hunk.new_start, hunk.new_length)} @@\n"
        )
        for prefix, text in hunk.lines:
            _emit(prefix, text, out)
    return "".join(out)


# ---------------------------------------------------------------- parsing -- #
@dataclass(frozen=True)
class Patch:
    """A parsed patch: names from the header plus its hunks."""

    from_name: str
    to_name: str
    hunks: tuple[Hunk, ...]

    def __len__(self) -> int:
        return len(self.hunks)


def parse_patch(text: str) -> Patch:
    """Parse a unified diff, refusing anything ambiguous.

        >>> patch = parse_patch(diff("a\\n", "b\\n"))
        >>> len(patch), patch.hunks[0].old_lines, patch.hunks[0].new_lines
        (1, ('a\\n',), ('b\\n',))
        >>> parse_patch("")
        Patch(from_name='a', to_name='b', hunks=())
    """
    lines = text.splitlines(keepends=True)
    index = 0
    from_name, to_name = "a", "b"

    if index < len(lines) and lines[index].startswith("--- "):
        from_name = lines[index][4:].strip() or "a"
        index += 1
    if index < len(lines) and lines[index].startswith("+++ "):
        to_name = lines[index][4:].strip() or "b"
        index += 1

    hunks: list[Hunk] = []
    while index < len(lines):
        line = lines[index]
        if not line.startswith("@@"):
            if line.strip() == "":
                index += 1
                continue
            raise PatchError(f"line {index + 1}: expected a hunk header, got {line!r}")

        match = HUNK_HEADER.match(line)
        if not match:
            raise PatchError(f"line {index + 1}: malformed hunk header: {line.strip()!r}")
        old_start = int(match.group(1))
        old_length = int(match.group(2) or 1)
        new_start = int(match.group(3))
        new_length = int(match.group(4) or 1)
        index += 1

        body: list[tuple[str, str]] = []
        old_seen = new_seen = 0
        # Consume every prefixed line (and any no-newline marker) until the next
        # header or the end: the counts in the header are a check, not the loop guard.
        while index < len(lines):
            body_line = lines[index]
            if body_line.startswith(NO_NEWLINE):
                if not body:
                    raise PatchError(f"line {index + 1}: stray no-newline marker")
                prefix, previous = body[-1]
                body[-1] = (prefix, previous.rstrip("\n"))
                index += 1
                continue
            if not body_line or body_line[0] not in " -+":
                break  # the next header (or trailing garbage) ends this hunk
            prefix, payload = body_line[0], body_line[1:]
            if prefix in " -":
                old_seen += 1
            if prefix in " +":
                new_seen += 1
            body.append((prefix, payload))
            index += 1

        if old_seen != old_length or new_seen != new_length:
            raise PatchError(
                f"hunk at line {index + 1}: header says "
                f"{-old_length,+new_length} but the body has {-old_seen,+new_seen}"
            )
        hunks.append(Hunk(old_start, old_length, new_start, new_length, tuple(body)))

    return Patch(from_name=from_name, to_name=to_name, hunks=tuple(hunks))


# ---------------------------------------------------------------- applying -- #
def _find(original: Sequence[str], expected: Sequence[str], guess: int) -> int | None:
    """Exact match only: try the guess, then every other offset, nearest first."""
    if not expected:
        return min(max(guess, 0), len(original))
    limit = len(original) - len(expected)
    for offset in range(0, max(len(original), 1)):
        for candidate in (guess - offset, guess + offset):
            if 0 <= candidate <= limit and list(original[candidate : candidate + len(expected)]) == list(expected):
                return candidate
    return None


def apply_patch(original: str, patch_text: str) -> str:
    """Apply a unified diff, or raise :class:`PatchError` without changing anything.

        >>> apply_patch("a\\nb\\n", diff("a\\nb\\n", "a\\nc\\n"))
        'a\\nc\\n'
        >>> apply_patch("a\\nb\\n", "")
        'a\\nb\\n'

    A drifted file still applies — the hunk is found by content, and the drift is
    carried forward so later hunks stay aligned:

        >>> patch = diff("x\\na\\n", "x\\nb\\n", context=1)
        >>> print(apply_patch("header\\nx\\na\\n", patch), end="")
        header
        x
        b
    """
    patch = parse_patch(patch_text)
    if not patch.hunks:
        return original

    lines = _split(original)
    result: list[str] = []
    position = 0
    drift = 0

    for number, hunk in enumerate(patch.hunks, start=1):
        expected = hunk.old_lines
        # A zero-length side means "insert here": its start is a count of lines
        # before the insertion point, not a 1-based line number.
        base = hunk.old_start - 1 if hunk.old_length else hunk.old_start
        guess = base + drift
        found = _find(lines, expected, guess)
        if found is None:
            raise PatchError(
                f"hunk {number} does not apply: expected {len(expected)} line(s) starting at "
                f"line {hunk.old_start} ({_preview(expected)})"
            )
        if found < position:
            raise PatchError(f"hunk {number} overlaps an earlier hunk")

        result.extend(lines[position:found])
        for prefix, text in hunk.lines:
            if prefix in " +":
                result.append(text)
        position = found + len(expected)
        drift = found - base

    result.extend(lines[position:])
    return "".join(result)


def _preview(lines: Sequence[str], limit: int = 60) -> str:
    joined = "".join(lines).replace("\n", "\\n")
    return joined if len(joined) <= limit else joined[: limit - 1] + "…"


def summarize(patch_text: str) -> str:
    """A one-line description, used by the CLI: ``3 hunk(s), +12 -4``."""
    patch = parse_patch(patch_text)
    added = sum(1 for hunk in patch.hunks for prefix, _ in hunk.lines if prefix == "+")
    removed = sum(1 for hunk in patch.hunks for prefix, _ in hunk.lines if prefix == "-")
    return f"{len(patch.hunks)} hunk(s), +{added} -{removed}"


def first_lines(text: str, count: int) -> Iterable[str]:  # pragma: no cover - CLI helper
    return _split(text)[:count]
