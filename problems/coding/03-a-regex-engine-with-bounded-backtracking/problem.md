---
title: A regex engine with bounded backtracking
category: coding
level: hard
concepts: parser, Thompson NFA, backtracking, capture groups, ReDoS budgets
status: solved
---

# A regex engine with bounded backtracking

## Context

`re` is one `import` away, and it is still the wrong answer here, because the
interesting part is not "match a string" — it is *what the engine does when the
pattern is hostile*.

Two facts make a regex engine a production problem:

1. **Backtracking is exponential in the pattern, not in the input.** `(a+)+b`
   against thirty `a`s is not slow; it is a denial-of-service waiting for one
   attacker-supplied header. Engines either forbid it (RE2, Thompson NFA, linear
   time) or bound it (PCRE's `match_limit`, .NET's timeout).
2. **A regex that does not match is a debugging problem.** "It doesn't work" is
   not a bug report; `missing ')' for the group opened at position 0` is.

So: parse a real pattern syntax, compile it to a program, run it with a step
budget, and be able to show both the tree and the bytecode.

```
pattern -> parser -> AST -> compiler -> program -> VM -> Match
```

## Behaviour

```bash
python -m solution.cli '(\w+)@(\w+)' --text 'ada@example.com'
# 'ada@example' at 0-11
#   group 1: 'ada'
#   group 2: 'example'

python -m solution.cli '\d+' --text 'a1b22' --findall      # 1, then 22
python -m solution.cli 'a+(b|c)' --explain                # the AST
python -m solution.cli 'a+(b|c)' --disasm                 # the program
python -m solution.cli '(a+)+b' --text aaaaaaaaaaaaaaaaaaaa --budget 5000
# error: pattern '(a+)+b' exceeded the budget of 5000 steps ...  (exit 2)
python -m solution.cli '(a'                                # caret under the error
```

Supported syntax:

| Syntax | Meaning |
|---|---|
| `abc` | literal characters |
| `.` | any character except a newline |
| `[a-z0-9_]`, `[^abc]` | character classes and ranges |
| `\d \D \w \W \s \S` | shorthand classes (outside brackets) |
| `*` `+` `?` | greedy quantifiers |
| `*?` `+?` `??` | lazy quantifiers |
| `{m}` `{m,}` `{m,n}` (+ `?`) | counted quantifiers, greedy or lazy |
| `(...)` | capturing group, numbered from 1 |
| `(?:...)` | non-capturing group |
| `a\|b` | alternation, leftmost branch first |
| `^` `$` | anchors (per line with `multiline=True`) |
| `\b` `\B` | word boundary / not a boundary |

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | A hand-written recursive-descent parser: alternation → concatenation → repeat → atom | the grammar *is* the documentation; precedence bugs hide in shortcuts |
| 2 | Quantifiers are greedy by default and lazy with a trailing `?` | `<.*>` and `<.*?>` must behave differently, or HTML scraping is broken |
| 3 | Leftmost match wins, and among equals the leftmost *branch* wins | `ab\|abc` on `abc` is `ab`, exactly like `re` |
| 4 | Capturing groups are numbered by opening parenthesis; `(?:...)` does not shift them | backreference bookkeeping is where hand-rolled engines rot |
| 5 | An unmatched optional group is `None`, not `""` | `"a"` vs *did not participate* are different answers |
| 6 | Character classes support ranges, negation, a literal `]` first, and a literal `-` last | `[]]`, `[a-]`, `[^]]` are in every real pattern |
| 7 | Shorthand classes inside `[...]` are refused with an explanation | `[\d]` looks right and means something else; guessing is worse than failing |
| 8 | Compile to a program of small instructions (`char`, `class`, `split`, `jmp`, `save`, `bol`, `eol`, `boundary`) | a VM you can print is a VM you can debug |
| 9 | The VM is iterative, with an explicit backtrack stack — not Python recursion | a 10 kB input must not hit `RecursionError` |
| 10 | Every executed instruction costs one step; exceeding `budget` raises `BudgetExceeded` | the whole ReDoS lesson in one class |
| 11 | The budget error names the pattern and the limit and says *why* | "took too long" is not actionable |
| 12 | `PatternError` carries the character position and a message with the opening position for unbalanced groups | errors are the feature |
| 13 | `^`/`$` are whole-text by default and line-relative with `multiline=True`; `.` never matches `\n` | the two flags everyone misremembers |
| 14 | `finditer` advances past empty matches instead of looping forever | `b*` on `ab` has three matches, one of them empty |
| 15 | `ignore_case` applies to literals *and* class ranges | a flag that only half works is a bug factory |
| 16 | `explain()` prints the AST and `disassemble()` prints the program, both deterministic | the two debugging tools a regex author actually needs |
| 17 | `escape()` quotes every metacharacter, and `compile(escape(x))` matches `x` | the only safe way to embed user text |
| 18 | A pattern that compiles to more than 20 000 instructions is refused | `a{1000}{1000}` must not be a memory bomb |

## Constraints & acceptance criteria

- No `re` anywhere in the solution: the point is the engine, and a hidden
  `re.search` would make every test pass while teaching nothing. (The test file
  does not import `re` either.)
- `search`, `match` and `fullmatch` are distinct: `match` is anchored at 0 and
  never tries a later start position, `fullmatch` additionally requires `end ==
  len(text)`.
- The engine never mutates the pattern or the text; a compiled `Regex` is safe to
  reuse and to share.
- Determinism: the same pattern, text and budget always produce the same match,
  the same spans and the same step count.
- `sub` replaces with a literal string — no `\1` expansion, which is documented
  rather than half-implemented.

## Assumptions (write them down — real problems are under-specified)

- Backreferences (`\1`) and lookaround (`(?=` `(?!` `(?<=`) are out of scope;
  both need a different execution model, and `(?...)` today only means `(?:`.
- No Unicode properties (`\p{L}`): `\w` is ASCII plus `_`, and that is stated in
  the module docstring rather than discovered by a user in production.
- `.` does not match `\n`, no `DOTALL` flag. `multiline` splits anchors only.
- Counts above 1000 are refused (`a{1001}`) — considered a pattern bug, not a
  feature request.
- Alternation is not optimised into a DFA; the engine is a backtracker with a
  budget, and pretending otherwise would be a lie in the README.

## How to run

```bash
cd problems/coding/03-a-regex-engine-with-bounded-backtracking
python -m unittest discover -s tests -t .
python -m solution.cli 'a.*?b' --text 'a1213b4b'
python -m solution.cli '(a+)+b' --text aaaaaaaaaaaaaaaaaaaa --budget 5000; echo "exit: $?"
python -m solution.cli '(a+)+b' --text aaaaaaaaaaaaaaaaaaaa            # default budget also stops
python -m solution.cli '[a-c]+' --text xxabcxx --findall
```

## Stretch goals

- Compile to a Pike VM (threads, no backtracking) and show that `(a+)+b` becomes
  linear — the same program shape, a different executor.
- A `--steps` flag that prints how many instructions a match cost, so the budget
  can be sized from measurements instead of fear.
- Named groups `(?P<name>...)` and a `Match.group("name")` lookup.
- Port the test corpus against Python's `re` as an oracle, excluding the
  documented differences.
