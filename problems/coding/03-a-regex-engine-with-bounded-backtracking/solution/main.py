"""A small regular-expression engine: parser, compiler, and a budgeted VM.

This is not ``re`` and it does not try to be. It is the *shape* of one, written
out so the interesting parts are visible:

    source text  ->  parser  ->  AST  ->  compiler  ->  program  ->  VM  ->  Match

Supported syntax
----------------
============================  ==================================================
``abc``                       literal characters
``.``                         any character except a newline
``[a-z0-9_]`` ``[^abc]``      character classes and ranges
``\\d \\D \\w \\W \\s \\S``      shorthand classes (outside classes)
``*`` ``+`` ``?``             greedy quantifiers
``*?`` ``+?`` ``??``          lazy quantifiers
``{m}`` ``{m,}`` ``{m,n}``    counted quantifiers, greedy or lazy
``(...)``                     capturing group, numbered from 1
``(?:...)``                   non-capturing group
``a|b``                       alternation, leftmost branch first
``^`` ``$``                   anchors (whole text, or per line with ``multiline``)
``\\b`` ``\\B``                 word boundary / not a boundary
============================  ==================================================

The two rules that make it interesting:

* **Backtracking is bounded.** Every instruction the VM executes costs one step.
  ``BudgetExceeded`` is raised instead of hanging, which is the whole ReDoS story:
  ``(a+)+b`` against thirty ``a``s is not slow, it is *wrong*, and the engine says
  so in milliseconds.
* **Errors point at the pattern.** ``PatternError`` carries the position and a
  message a human can act on.

Example
-------
>>> match = search(r"(\\w+)@(\\w+)\\.com", "write to ada@example.com today")
>>> match.group(0), match.group(1), match.group(2)
('ada@example.com', 'ada', 'example')
>>> match.span()
(9, 24)
>>> findall(r"\\d+", "port 80, port 443")
['80', '443']
>>> bool(fullmatch(r"a{2,4}", "aaa")), bool(fullmatch(r"a{2,4}", "a"))
(True, False)
>>> compile(r"^b").search("abc") is None
True
>>> compile(r"^a", multiline=True).search("xx\\nab")
<Match 'a' at 3>
>>> compile("ADA", ignore_case=True).search("ada").group(0)
'ada'
>>> escape("a.b*c")
'a\\\\.b\\\\*c'
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence

DEFAULT_BUDGET = 100_000
MAX_PROGRAM = 20_000
_WHITESPACE = " \t\n\r\f\v"
_SHORTHAND: dict[str, tuple[bool, tuple[tuple[str, str], ...]]] = {
    "d": (False, (("0", "9"),)),
    "w": (False, (("0", "9"), ("A", "Z"), ("_", "_"), ("a", "z"))),
    "s": (False, ((" ", " "), ("\t", "\t"), ("\n", "\n"), ("\r", "\r"), ("\f", "\f"), ("\v", "\v"))),
}
_META = set("\\^$.*+?()[]{}|")
_WORD = frozenset("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")


class PatternError(ValueError):
    """The pattern does not parse. Carries the position it failed at.

    >>> try:
    ...     compile("(a")
    ... except PatternError as error:
    ...     print(error)
    ...     print("failed at", error.position)
    missing ')' for the group opened at position 0 (at position 2)
    failed at 2
    """

    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"{message} (at position {position})")
        self.message = message
        self.position = position

    def __str__(self) -> str:  # pragma: no cover - exercised through str()
        return f"{self.message} (at position {self.position})"


class BudgetExceeded(RuntimeError):
    """The pattern needed more VM steps than the caller allowed.

    >>> try:
    ...     compile("(a+)+b").search("a" * 20, budget=2_000)
    ... except BudgetExceeded as error:
    ...     print(error.budget)
    2000
    """

    def __init__(self, pattern: str, budget: int) -> None:
        super().__init__(
            f"pattern {pattern!r} exceeded the budget of {budget} steps — "
            "it can backtrack exponentially on this input"
        )
        self.pattern = pattern
        self.budget = budget


# ------------------------------------------------------------------------ ast --


@dataclass(frozen=True)
class Literal:
    char: str


@dataclass(frozen=True)
class Any:
    """``.`` — any character except a newline."""


@dataclass(frozen=True)
class CharClass:
    negated: bool
    ranges: tuple[tuple[str, str], ...]

    @property
    def source(self) -> str:
        body = "".join(a if a == b else f"{a}-{b}" for a, b in self.ranges)
        return f"[{'^' if self.negated else ''}{body}]"


@dataclass(frozen=True)
class Anchor:
    kind: str  # "start" | "end" | "word" | "not-word"


@dataclass(frozen=True)
class Concat:
    parts: tuple[object, ...]


@dataclass(frozen=True)
class Alt:
    branches: tuple[object, ...]


@dataclass(frozen=True)
class Group:
    node: object
    index: int | None  # None for (?:...)


@dataclass(frozen=True)
class Repeat:
    node: object
    minimum: int
    maximum: int | None
    lazy: bool = False


# --------------------------------------------------------------------- parser --


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.position = 0
        self.groups = 0
        self.open_groups: list[int] = []

    # -- helpers ----------------------------------------------------------
    def peek(self, offset: int = 0) -> str:
        index = self.position + offset
        return self.source[index] if index < len(self.source) else ""

    def take(self) -> str:
        char = self.source[self.position]
        self.position += 1
        return char

    def fail(self, message: str, position: int | None = None) -> None:
        raise PatternError(message, self.position if position is None else position)

    def at_end(self) -> bool:
        return self.position >= len(self.source)

    # -- grammar ----------------------------------------------------------
    def parse(self) -> object:
        node = self.parse_alternation()
        if not self.at_end():
            self.fail(f"unexpected {self.peek()!r} — no matching '(' is open")
        return node

    def parse_alternation(self) -> object:
        branches = [self.parse_concat()]
        while self.peek() == "|":
            self.take()
            branches.append(self.parse_concat())
        return branches[0] if len(branches) == 1 else Alt(tuple(branches))

    def parse_concat(self) -> object:
        parts: list[object] = []
        while not self.at_end() and self.peek() not in "|)":
            parts.append(self.parse_repeat())
        if not parts:
            return Concat(())
        return parts[0] if len(parts) == 1 else Concat(tuple(parts))

    def parse_repeat(self) -> object:
        start = self.position
        node = self.parse_atom()
        repeated = False
        while True:
            char = self.peek()
            if repeated and (char in ("*", "+", "?") or char == "{"):
                self.fail("multiple repeat: wrap the expression in a group to quantify it twice")
            if char in ("*", "+", "?"):
                self.take()
                minimum, maximum = {"*": (0, None), "+": (1, None), "?": (0, 1)}[char]
                lazy = self._take_lazy()
                node = Repeat(node, minimum, maximum, lazy)
                repeated = True
            elif char == "{":
                counted = self._parse_counted(start)
                if counted is None:
                    break
                minimum, maximum = counted
                lazy = self._take_lazy()
                node = Repeat(node, minimum, maximum, lazy)
                repeated = True
            else:
                break
        return node

    def _take_lazy(self) -> bool:
        if self.peek() == "?":
            self.take()
            return True
        return False

    def _parse_counted(self, atom_start: int) -> tuple[int, int | None] | None:
        """Parse ``{m}``, ``{m,}`` or ``{m,n}``; ``None`` means "not a quantifier"."""
        saved = self.position
        self.take()  # "{"
        digits = ""
        while self.peek().isdigit():
            digits += self.take()
        if not digits:
            self.position = saved
            return None
        minimum = int(digits)
        maximum: int | None = minimum
        if self.peek() == ",":
            self.take()
            digits = ""
            while self.peek().isdigit():
                digits += self.take()
            maximum = int(digits) if digits else None
        if self.peek() != "}":
            self.fail("unterminated counted quantifier, expected '}'")
        self.take()
        if maximum is not None and maximum < minimum:
            self.fail(f"bad counted quantifier {{{minimum},{maximum}}}: the minimum is larger")
        if minimum > 1000 or (maximum is not None and maximum > 1000):
            self.fail("counted quantifier is too large (limit 1000)", atom_start)
        return minimum, maximum

    def parse_atom(self) -> object:
        char = self.peek()
        if char == "":
            self.fail("nothing to repeat: the pattern ends where an expression was expected")
        if char in "*+?":
            self.fail(f"nothing to repeat before {char!r}")
        if char == "(":
            opening = self.position
            self.take()
            self.open_groups.append(opening)
            index: int | None = None
            if self.peek() == "?":
                if self.peek(1) == ":":
                    self.take()
                    self.take()
                else:
                    self.fail("only (?:...) is supported after '(' and '?'", self.position)
            else:
                self.groups += 1
                index = self.groups
            inner = self.parse_alternation()
            if self.peek() != ")":
                self.fail(f"missing ')' for the group opened at position {opening}")
            self.take()
            self.open_groups.pop()
            return Group(inner, index)
        if char == "[":
            return self.parse_class()
        if char == ".":
            self.take()
            return Any()
        if char == "^":
            self.take()
            return Anchor("start")
        if char == "$":
            self.take()
            return Anchor("end")
        if char == "\\":
            return self.parse_escape()
        if char in "{}":
            self.fail(f"unexpected {char!r} — escape it as \\{char} to match it literally")
        self.take()
        return Literal(char)

    def parse_escape(self) -> object:
        start = self.position
        self.take()  # backslash
        if self.at_end():
            self.fail("a backslash at the end of the pattern escapes nothing", start)
        char = self.take()
        lowered = char.lower()
        if lowered in _SHORTHAND:
            negated, ranges = _SHORTHAND[lowered]
            return CharClass(negated=(char.isupper() != negated), ranges=ranges)
        if char == "b":
            return Anchor("word")
        if char == "B":
            return Anchor("not-word")
        if char in "nrt":
            return Literal({"n": "\n", "r": "\r", "t": "\t"}[char])
        if char in _META or char in "/-":
            return Literal(char)
        self.fail(f"unknown escape \\{char}", start)

    def parse_class(self) -> object:
        start = self.position
        self.take()  # "["
        negated = False
        if self.peek() == "^":
            negated = True
            self.take()
        items: list[tuple[str, str]] = []
        first = True
        while True:
            if self.at_end():
                self.fail("unterminated character class, expected ']'", start)
            char = self.peek()
            if char == "]" and not first:
                self.take()
                break
            first = False
            low = self._class_char()
            if self.peek() == "-" and self.peek(1) not in ("", "]"):
                self.take()
                high = self._class_char()
                if high < low:
                    self.fail(f"bad range {low}-{high}: the end is before the start", self.position)
                items.append((low, high))
            else:
                items.append((low, low))
        return CharClass(negated, tuple(items))

    def _class_char(self) -> str:
        """One literal or escaped character inside ``[...]``."""
        start = self.position
        char = self.take()
        if char != "\\":
            return char
        if self.at_end():
            self.fail("a backslash at the end of the pattern escapes nothing", start)
        escaped = self.take()
        lowered = escaped.lower()
        if lowered in _SHORTHAND:
            self.fail(
                f"\\{escaped} is a whole class and cannot sit inside [...]; "
                "write the range out or use it outside the brackets",
                start,
            )
        if escaped in "nrt":
            return {"n": "\n", "r": "\r", "t": "\t"}[escaped]
        if escaped in _META or escaped in "/-^":
            return escaped
        self.fail(f"unknown escape \\{escaped} inside a character class", start)


# ------------------------------------------------------------------ compiler --


class _Compiler:
    def __init__(self) -> None:
        self.program: list[tuple] = []

    def emit(self, instruction: tuple) -> int:
        self.program.append(instruction)
        return len(self.program) - 1

    def compile(self, node: object) -> None:
        if isinstance(node, Literal):
            self.emit(("char", node.char))
        elif isinstance(node, Any):
            self.emit(("any",))
        elif isinstance(node, CharClass):
            self.emit(("class", node.negated, node.ranges))
        elif isinstance(node, Anchor):
            self.emit({"start": ("bol",), "end": ("eol",), "word": ("boundary",), "not-word": ("not-boundary",)}[node.kind])
        elif isinstance(node, Concat):
            for part in node.parts:
                self.compile(part)
        elif isinstance(node, Alt):
            jumps: list[int] = []
            for index, branch in enumerate(node.branches):
                last = index == len(node.branches) - 1
                if not last:
                    split = self.emit(("split", None, None))
                    body = len(self.program)
                    self.compile(branch)
                    jumps.append(self.emit(("jmp", None)))
                    self.program[split] = ("split", body, len(self.program))
                else:
                    self.compile(branch)
            end = len(self.program)
            for jump in jumps:
                self.program[jump] = ("jmp", end)
        elif isinstance(node, Group):
            if node.index is None:
                self.compile(node.node)
            else:
                self.emit(("save", 2 * node.index))
                self.compile(node.node)
                self.emit(("save", 2 * node.index + 1))
        elif isinstance(node, Repeat):
            self.compile_repeat(node)
        else:  # pragma: no cover - every AST node is handled above
            raise AssertionError(f"cannot compile {node!r}")

    def compile_repeat(self, node: Repeat) -> None:
        for _ in range(node.minimum):
            self.compile(node.node)
        if node.maximum is None:
            start = len(self.program)
            split = self.emit(("split", None, None))
            body = len(self.program)
            self.compile(node.node)
            self.emit(("jmp", start))
            after = len(self.program)
            self.program[split] = ("split", after, body) if node.lazy else ("split", body, after)
            return
        splits: list[tuple[int, int]] = []
        for _ in range(node.maximum - node.minimum):
            split = self.emit(("split", None, None))
            body = len(self.program)
            self.compile(node.node)
            splits.append((split, body))
        after = len(self.program)
        for split, body in splits:
            self.program[split] = ("split", after, body) if node.lazy else ("split", body, after)


# ---------------------------------------------------------------------- match --


@dataclass(frozen=True)
class Match:
    """A successful match: the text, the spans, and the groups."""

    text: str
    spans: tuple[tuple[int, int], ...]
    pattern: str = field(default="", compare=False)

    @property
    def start(self) -> int:
        return self.spans[0][0]

    @property
    def end(self) -> int:
        return self.spans[0][1]

    def span(self, group: int = 0) -> tuple[int, int]:
        return self.spans[group]

    def group(self, group: int = 0) -> str | None:
        """The text a group matched, or ``None`` when it did not participate.

        >>> match = compile(r"(a)(b)?").search("a")
        >>> match.group(0), match.group(1), match.group(2)
        ('a', 'a', None)
        """
        start, end = self.spans[group]
        return None if start < 0 else self.text[start:end]

    def groups(self) -> tuple[str | None, ...]:
        return tuple(self.group(index) for index in range(1, len(self.spans)))

    def __repr__(self) -> str:
        return f"<Match {self.group(0)!r} at {self.start}>"


# ------------------------------------------------------------------- engine --


class Regex:
    """A compiled pattern. Matching is a loop over a small instruction set."""

    def __init__(
        self,
        source: str,
        program: Sequence[tuple],
        groups: int,
        *,
        ignore_case: bool = False,
        multiline: bool = False,
    ) -> None:
        self.source = source
        self.program = list(program)
        self.groups = groups
        self.ignore_case = ignore_case
        self.multiline = multiline

    # -- matching ---------------------------------------------------------
    def _same(self, left: str, right: str) -> bool:
        if self.ignore_case:
            return left.lower() == right.lower()
        return left == right

    def _in_class(self, char: str, negated: bool, ranges: Sequence[tuple[str, str]]) -> bool:
        candidate = char.lower() if self.ignore_case else char
        found = any(low <= candidate <= high for low, high in ranges)
        if self.ignore_case:
            swapped = char.upper()
            found = found or any(low <= swapped <= high for low, high in ranges)
        return found != negated

    def _boundary(self, text: str, position: int) -> bool:
        before = text[position - 1] if position > 0 else ""
        after = text[position] if position < len(text) else ""
        return (before in _WORD) != (after in _WORD)

    def _run(self, text: str, start: int, budget: int) -> Match | None:
        program = self.program
        length = len(text)
        initial = tuple([-1] * (2 * (self.groups + 1)))
        stack: list[tuple[int, int, tuple[int, ...]]] = [(0, start, initial)]
        steps = 0

        while stack:
            pc, position, caps = stack.pop()
            while True:
                steps += 1
                if steps > budget:
                    raise BudgetExceeded(self.source, budget)
                instruction = program[pc]
                op = instruction[0]

                if op == "char":
                    if position < length and self._same(text[position], instruction[1]):
                        pc += 1
                        position += 1
                        continue
                    break
                if op == "any":
                    if position < length and text[position] != "\n":
                        pc += 1
                        position += 1
                        continue
                    break
                if op == "class":
                    if position < length and self._in_class(text[position], instruction[1], instruction[2]):
                        pc += 1
                        position += 1
                        continue
                    break
                if op == "split":
                    stack.append((instruction[2], position, caps))
                    pc = instruction[1]
                    continue
                if op == "jmp":
                    pc = instruction[1]
                    continue
                if op == "save":
                    caps = caps[: instruction[1]] + (position,) + caps[instruction[1] + 1 :]
                    pc += 1
                    continue
                if op == "bol":
                    if position == 0 or (self.multiline and text[position - 1] == "\n"):
                        pc += 1
                        continue
                    break
                if op == "eol":
                    if position == length or (self.multiline and text[position] == "\n"):
                        pc += 1
                        continue
                    break
                if op in ("boundary", "not-boundary"):
                    if self._boundary(text, position) == (op == "boundary"):
                        pc += 1
                        continue
                    break
                if op == "match":
                    return Match(text, tuple((caps[i], caps[i + 1]) for i in range(0, len(caps), 2)), self.source)
                raise AssertionError(f"unknown instruction {instruction!r}")  # pragma: no cover
        return None

    def search(self, text: str, *, budget: int = DEFAULT_BUDGET, start: int = 0) -> Match | None:
        """Leftmost match, with the leftmost-first branch order of the pattern.

        >>> compile("ab|abc").search("abc").group(0)
        'ab'
        >>> compile("ab|abc", ).search("xabc").span()
        (1, 3)
        """
        for position in range(start, len(text) + 1):
            match = self._run(text, position, budget)
            if match is not None:
                return match
        return None

    def match(self, text: str, *, budget: int = DEFAULT_BUDGET) -> Match | None:
        """Anchored at the start, like ``re.match``: no later position is tried."""
        return self._run(text, 0, budget)

    def fullmatch(self, text: str, *, budget: int = DEFAULT_BUDGET) -> Match | None:
        match = self.match(text, budget=budget)
        return match if match is not None and match.end == len(text) else None

    def finditer(self, text: str, *, budget: int = DEFAULT_BUDGET) -> Iterator[Match]:
        position = 0
        while position <= len(text):
            match = self.search(text, budget=budget, start=position)
            if match is None:
                return
            yield match
            # An empty match must still advance, or this loop never ends.
            position = match.end if match.end > match.start else match.start + 1

    def findall(self, text: str, *, budget: int = DEFAULT_BUDGET) -> list[str]:
        return [match.group(0) or "" for match in self.finditer(text, budget=budget)]

    def split(self, text: str, *, budget: int = DEFAULT_BUDGET) -> list[str]:
        """Cut the text at every match; the matches themselves are dropped.

        >>> compile(r"\s+").split("a  b\tc")
        ['a', 'b', 'c']
        """
        pieces: list[str] = []
        cursor = 0
        for match in self.finditer(text, budget=budget):
            pieces.append(text[cursor : match.start])
            cursor = match.end
        pieces.append(text[cursor:])
        return pieces

    def sub(self, replacement: str, text: str, *, budget: int = DEFAULT_BUDGET) -> str:
        """Replace every match. The replacement is literal — no ``\\1`` backreferences.

        >>> compile(r"\d+").sub("#", "a1b22")
        'a#b#'
        """
        out: list[str] = []
        cursor = 0
        for match in self.finditer(text, budget=budget):
            out.append(text[cursor : match.start])
            out.append(replacement)
            cursor = match.end
        out.append(text[cursor:])
        return "".join(out)

    # -- introspection ----------------------------------------------------
    def explain(self) -> str:
        """The AST, one node per line — the debugging tool this problem is for.

        >>> print(compile("ab|c").explain())
        ALT
          SEQ
            'a'
            'b'
          'c'
        """
        lines: list[str] = []
        _explain(_parse(self.source), 0, lines)
        return "\n".join(lines)

    def disassemble(self) -> list[str]:
        return [f"{index:>3}  {_render_instruction(instruction)}" for index, instruction in enumerate(self.program)]


def _render_instruction(instruction: tuple) -> str:
    op = instruction[0]
    if op == "char":
        return f"char {instruction[1]!r}"
    if op == "class":
        body = "".join(a if a == b else f"{a}-{b}" for a, b in instruction[2])
        return f"class [{'^' if instruction[1] else ''}{body}]"
    if op == "split":
        return f"split {instruction[1]} {instruction[2]}"
    if op in ("jmp", "save"):
        return f"{op} {instruction[1]}"
    return op


def _explain(node: object, depth: int, lines: list[str]) -> None:
    pad = "  " * depth
    if isinstance(node, Literal):
        lines.append(f"{pad}'{node.char}'")
    elif isinstance(node, Any):
        lines.append(f"{pad}ANY")
    elif isinstance(node, CharClass):
        lines.append(f"{pad}CLASS {node.source}")
    elif isinstance(node, Anchor):
        lines.append(f"{pad}{node.kind.upper()}")
    elif isinstance(node, Concat):
        lines.append(f"{pad}SEQ")
        for part in node.parts:
            _explain(part, depth + 1, lines)
    elif isinstance(node, Alt):
        lines.append(f"{pad}ALT")
        for branch in node.branches:
            _explain(branch, depth + 1, lines)
    elif isinstance(node, Group):
        label = f"GROUP {node.index}" if node.index is not None else "GROUP (?:)"
        lines.append(f"{pad}{label}")
        _explain(node.node, depth + 1, lines)
    elif isinstance(node, Repeat):
        maximum = "inf" if node.maximum is None else node.maximum
        suffix = " lazy" if node.lazy else ""
        lines.append(f"{pad}REPEAT{{{node.minimum},{maximum}}}{suffix}")
        _explain(node.node, depth + 1, lines)


# ------------------------------------------------------------------- facade --


def _parse(pattern: str) -> object:
    return _Parser(pattern).parse()


def compile(  # noqa: A001  (this is the module's own namespace, not builtins)
    pattern: str, *, ignore_case: bool = False, multiline: bool = False
) -> Regex:
    """Parse and compile a pattern.

    >>> compile(r"a(b|c)+").disassemble()[:4]
    ['  0  save 0', "  1  char 'a'", '  2  save 2', '  3  split 4 6']
    """
    node = _parse(pattern)
    compiler = _Compiler()
    compiler.emit(("save", 0))
    compiler.compile(node)
    compiler.emit(("save", 1))
    compiler.emit(("match",))
    if len(compiler.program) > MAX_PROGRAM:
        raise PatternError(f"pattern compiles to more than {MAX_PROGRAM} instructions", 0)
    return Regex(
        pattern,
        compiler.program,
        _count_groups(pattern),
        ignore_case=ignore_case,
        multiline=multiline,
    )


def _count_groups(pattern: str) -> int:
    """Count capturing groups without trusting a second parse."""
    total = 0
    index = 0
    in_class = False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            if char == "]":
                in_class = False
            index += 1
            continue
        if char == "[":
            in_class = True
        elif char == "(" and (index + 1 >= len(pattern) or pattern[index + 1] != "?"):
            total += 1
        index += 1
    return total


def search(pattern: str, text: str, **flags: bool) -> Match | None:
    """Compile and search in one call.

    >>> search(r"\\d+", "abc 42").group(0)
    '42'
    """
    return compile(pattern, **flags).search(text)


def fullmatch(pattern: str, text: str, **flags: bool) -> Match | None:
    """Compile and match the whole text in one call."""
    return compile(pattern, **flags).fullmatch(text)


def findall(pattern: str, text: str, **flags: bool) -> list[str]:
    """Compile and find every non-overlapping match."""
    return compile(pattern, **flags).findall(text)


def escape(text: str) -> str:
    """Quote every metacharacter so ``text`` can be embedded in a pattern.

    >>> escape("1+1=2")
    '1\\\\+1=2'
    """
    return "".join(f"\\{char}" if char in _META else char for char in text)
