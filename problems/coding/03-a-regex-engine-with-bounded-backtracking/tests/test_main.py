"""Tests for the regex engine: parsing, semantics, captures, budgets, errors, CLI."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import (
    DEFAULT_BUDGET,
    BudgetExceeded,
    Match,
    PatternError,
    Regex,
    compile,
    escape,
    findall,
    fullmatch,
    search,
)


def spans(pattern: str, text: str, **flags: bool) -> tuple[tuple[int, int], ...]:
    match = compile(pattern, **flags).search(text)
    assert match is not None, f"{pattern!r} did not match {text!r}"
    return match.spans


class LiteralAndDotTest(unittest.TestCase):
    def test_literals_match_in_order(self) -> None:
        self.assertEqual(search("abc", "xxabcxx").group(0), "abc")

    def test_search_finds_the_leftmost_match(self) -> None:
        self.assertEqual(spans("ab", "abab"), ((0, 2),))

    def test_dot_matches_anything_but_a_newline(self) -> None:
        self.assertEqual(compile("a.c").search("abc").group(0), "abc")
        self.assertIsNone(compile("a.c").search("a\nc"))

    def test_dot_does_not_swallow_the_end_of_the_text(self) -> None:
        self.assertIsNone(compile("a.c").search("ab"))
        self.assertIsNotNone(compile("a.c").search("abcd"))

    def test_metacharacters_must_be_escaped_to_match_literally(self) -> None:
        self.assertIsNotNone(compile(r"1\+1").search("1+1"))
        self.assertIsNotNone(compile(r"a\.b").search("a.b"))


class ClassTest(unittest.TestCase):
    def test_ranges(self) -> None:
        self.assertEqual(compile("[a-c]+").findall("xxabcxx"), ["abc"])
        self.assertEqual(compile("[a-z0-9_]+").findall("A_1 b"), ["_1", "b"])

    def test_negated_class(self) -> None:
        self.assertEqual(compile("[^0-9]+").findall("abc123def"), ["abc", "def"])

    def test_shorthand_classes(self) -> None:
        self.assertEqual(findall(r"\d+", "a1b22"), ["1", "22"])
        self.assertEqual(findall(r"\w+", "héllo world_2"), ["h", "llo", "world_2"])
        self.assertEqual(findall(r"\s+", "a \t b"), [" \t "])
        self.assertEqual(findall(r"\D+", "a1b"), ["a", "b"])

    def test_class_edges(self) -> None:
        # "]" first, "-" at the end, and a "]" that closes.
        self.assertEqual(compile("[]]").findall("a]b"), ["]"])
        self.assertEqual(compile("[a-]+").findall("a-b"), ["a-"])
        self.assertEqual(compile("[^]]+").findall("[]x]"), ["[", "x"])

    def test_escaped_characters_inside_a_class(self) -> None:
        self.assertIsNotNone(compile(r"[\]]").search("]"))
        self.assertIsNotNone(compile(r"[\-]").search("-"))


class QuantifierTest(unittest.TestCase):
    def test_star_plus_question(self) -> None:
        self.assertEqual(compile("ab*").search("a").group(0), "a")
        self.assertEqual(compile("ab+").search("abbb").group(0), "abbb")
        self.assertIsNone(compile("ab+").search("a"))
        self.assertEqual(compile("ab?").search("ab").group(0), "ab")

    def test_greedy_takes_as_much_as_it_can(self) -> None:
        self.assertEqual(compile("a+").search("aaaa").group(0), "aaaa")
        self.assertEqual(compile("<.*>").search("<a><b>").group(0), "<a><b>")

    def test_lazy_takes_as_little_as_it_can(self) -> None:
        self.assertEqual(compile("<.*?>").search("<a><b>").group(0), "<a>")
        self.assertEqual(compile("a+?").search("aaaa").group(0), "a")
        self.assertEqual(compile("a??").search("aa").group(0), "")

    def test_counted_forms(self) -> None:
        self.assertEqual(compile("a{2}").findall("a aa aaa"), ["aa", "aa"])
        self.assertEqual(compile("a{2,}").search("aaaa").group(0), "aaaa")
        self.assertEqual(compile("a{2,3}").search("aaaa").group(0), "aaa")
        self.assertEqual(compile("a{0,1}").search("b").group(0), "")
        self.assertEqual(compile("a{3,5}?").search("aaaaa").group(0), "aaa")

    def test_counted_quantifier_applies_to_a_group(self) -> None:
        self.assertEqual(compile("(ab){2}").search("ababab").group(0), "abab")

    def test_backtracking_is_real(self) -> None:
        # The greedy ".*" must give characters back for the rest to match.
        self.assertEqual(compile("a.*b").search("a1213b4b").group(0), "a1213b4b")
        self.assertEqual(compile("a.*?b").search("a1213b4b").group(0), "a1213b")


class AlternationAndGroupTest(unittest.TestCase):
    def test_leftmost_branch_wins_even_if_a_later_one_is_longer(self) -> None:
        self.assertEqual(compile("ab|abc").search("abc").group(0), "ab")
        self.assertEqual(compile("abc|ab").search("abc").group(0), "abc")

    def test_group_numbering_and_nesting(self) -> None:
        match = compile(r"((a)(b))c").search("abc")
        self.assertEqual(match.groups(), ("ab", "a", "b"))

    def test_alternation_inside_a_group_keeps_the_outer_repeat(self) -> None:
        self.assertEqual(compile("(a|b)+").search("abab").group(0), "abab")

    def test_non_capturing_group_does_not_shift_numbers(self) -> None:
        match = compile(r"(?:ab)(c)").search("abc")
        self.assertEqual(match.groups(), ("c",))

    def test_unmatched_group_is_none(self) -> None:
        match = compile("(a)(b)?").search("a")
        self.assertIsNone(match.group(2))
        self.assertEqual(match.span(2), (-1, -1))

    def test_group_zero_is_the_whole_match(self) -> None:
        match = compile(r"(\d)(\d)").search("x42y")
        self.assertEqual(match.group(0), "42")
        self.assertEqual(match.span(), (1, 3))


class AnchorTest(unittest.TestCase):
    def test_start_and_end(self) -> None:
        self.assertIsNone(compile("^b").search("abc"))
        self.assertIsNotNone(compile("^a").search("abc"))
        self.assertIsNotNone(compile("c$").search("abc"))
        self.assertIsNone(compile("b$").search("abc"))

    def test_multiline_makes_anchors_line_relative(self) -> None:
        self.assertEqual(compile("^b$", multiline=True).findall("a\nb\nc"), ["b"])
        self.assertEqual(compile("^b", multiline=True).search("a\nbc").span(), (2, 3))

    def test_word_boundaries(self) -> None:
        self.assertEqual(compile(r"\bcat\b").findall("cat concat cat."), ["cat", "cat"])
        self.assertEqual(compile(r"\Bcat").findall("concat cat"), ["cat"])


class FlagTest(unittest.TestCase):
    def test_ignore_case_applies_to_literals(self) -> None:
        self.assertEqual(compile("ada", ignore_case=True).search("ADA").group(0), "ADA")

    def test_ignore_case_applies_to_classes(self) -> None:
        self.assertEqual(compile("[a-c]+", ignore_case=True).findall("xABCx"), ["ABC"])

    def test_ignore_case_is_off_by_default(self) -> None:
        self.assertIsNone(compile("ada").search("ADA"))


class ApiTest(unittest.TestCase):
    def test_findall_is_non_overlapping(self) -> None:
        self.assertEqual(findall("aa", "aaaaa"), ["aa", "aa"])

    def test_empty_matches_advance_the_cursor(self) -> None:
        self.assertEqual(compile("").findall("ab"), ["", "", ""])
        self.assertEqual(compile("b*").findall("ab"), ["", "b", ""])

    def test_split_and_sub(self) -> None:
        self.assertEqual(compile(r"\s+").split("a  b\tc"), ["a", "b", "c"])
        self.assertEqual(compile(r"\d+").sub("#", "a1b22"), "a#b#")

    def test_match_is_anchored_but_search_is_not(self) -> None:
        self.assertIsNone(compile("b").match("ab"))
        self.assertIsNotNone(compile("b").search("ab"))

    def test_fullmatch_requires_the_whole_text(self) -> None:
        self.assertIsNotNone(compile("ab").fullmatch("ab"))
        self.assertIsNone(compile("a").fullmatch("ab"))

    def test_match_repr_and_text_round_trip(self) -> None:
        match = compile("ab").search("xabx")
        self.assertIsInstance(match, Match)
        self.assertEqual(repr(match), "<Match 'ab' at 1>")

    def test_engine_reports_its_own_source(self) -> None:
        self.assertEqual(compile("a+").source, "a+")
        self.assertIsInstance(compile("a"), Regex)


class ErrorTest(unittest.TestCase):
    def assertError(self, pattern: str, fragment: str, position: int | None = None) -> PatternError:
        with self.assertRaises(PatternError) as caught:
            compile(pattern)
        message = str(caught.exception)
        self.assertIn(fragment, message)
        if position is not None:
            self.assertIn(f"at position {position}", message)
            self.assertEqual(caught.exception.position, position)
        return caught.exception

    def test_missing_closing_paren(self) -> None:
        self.assertError("(a", "missing ')' for the group opened at position 0")

    def test_unexpected_closing_paren(self) -> None:
        self.assertError("a)", "unexpected ')'")

    def test_unterminated_class(self) -> None:
        self.assertError("[abc", "unterminated character class", 0)

    def test_a_bracket_first_is_a_literal(self) -> None:
        self.assertError("[]", "unterminated character class")
        self.assertIsNotNone(compile("[]]").search("]"))

    def test_bad_range(self) -> None:
        self.assertError("[z-a]", "bad range z-a")

    def test_nothing_to_repeat(self) -> None:
        self.assertError("*a", "nothing to repeat before '*'")
        self.assertError("a**", "multiple repeat")
        self.assertError("a+{2}", "multiple repeat")

    def test_counted_quantifier_errors(self) -> None:
        self.assertError("a{3,2}", "the minimum is larger")
        self.assertError("a{2", "unterminated counted quantifier")
        self.assertError("a{1001}", "too large")

    def test_unknown_escape(self) -> None:
        self.assertError(r"\q", r"unknown escape \q")

    def test_trailing_backslash(self) -> None:
        self.assertError("\\", "escapes nothing")

    def test_shorthand_inside_a_class_is_refused(self) -> None:
        self.assertError(r"[\d]", r"\d is a whole class")

    def test_brace_needs_escaping_outside_a_quantifier(self) -> None:
        self.assertError("a{x}", "unexpected '{'")

    def test_question_mark_group_syntax_is_limited(self) -> None:
        self.assertError("(?=a)", "only (?:...) is supported")

    def test_error_position_is_a_character_offset(self) -> None:
        with self.assertRaises(PatternError) as caught:
            compile("ab(cd")
        self.assertEqual(caught.exception.position, len("ab(cd"))


class BudgetTest(unittest.TestCase):
    def test_catastrophic_backtracking_is_stopped(self) -> None:
        with self.assertRaises(BudgetExceeded):
            compile("(a+)+b").search("a" * 22, budget=20_000)

    def test_the_error_explains_itself(self) -> None:
        with self.assertRaises(BudgetExceeded) as caught:
            compile("(a+)+b").search("a" * 22, budget=1_000)
        self.assertIn("exceeded the budget", str(caught.exception))
        self.assertIn("backtrack exponentially", str(caught.exception))
        self.assertEqual(caught.exception.budget, 1_000)

    def test_a_linear_pattern_fits_a_small_budget(self) -> None:
        self.assertIsNotNone(compile(r"\d+").search("x" * 100 + "42", budget=5_000))

    def test_the_same_pattern_succeeds_with_more_budget(self) -> None:
        self.assertIsNotNone(compile("(a+)+b").search("a" * 8 + "b", budget=100_000))
        self.assertEqual(DEFAULT_BUDGET, 100_000)


class IntrospectionTest(unittest.TestCase):
    def test_explain_prints_the_ast(self) -> None:
        self.assertEqual(
            compile("a+(b|c)").explain(),
            "SEQ\n"
            "  REPEAT{1,inf}\n"
            "    'a'\n"
            "  GROUP 1\n"
            "    ALT\n"
            "      'b'\n"
            "      'c'",
        )

    def test_explain_marks_lazy_repeats_and_classes(self) -> None:
        self.assertEqual(
            compile("[a-c]+?x").explain(), "SEQ\n  REPEAT{1,inf} lazy\n    CLASS [a-c]\n  'x'"
        )

    def test_disassemble_is_numbered_and_readable(self) -> None:
        lines = compile("ab").disassemble()
        self.assertEqual(lines[0], "  0  save 0")
        self.assertIn("char 'a'", lines[1])
        self.assertTrue(lines[-1].endswith("match"), lines[-1])

    def test_escape_round_trips_through_the_parser(self) -> None:
        for text in ("a.b*c", "1+1=2", "x[y]z", "back\\slash", "(paren)"):
            with self.subTest(text=text):
                self.assertIsNotNone(compile(escape(text)).search(text))

    def test_escape_leaves_ordinary_text_alone(self) -> None:
        self.assertEqual(escape("plain text"), "plain text")


class CliTest(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_single_match_prints_groups(self) -> None:
        code, out, _ = self.run_cli([r"(\w+)@(\w+)", "--text", "ada@example.com"])
        self.assertEqual(code, 0)
        self.assertIn("'ada@example' at 0-11", out)
        self.assertIn("group 1: 'ada'", out)

    def test_findall_prints_one_per_line(self) -> None:
        code, out, _ = self.run_cli([r"\d+", "--text", "a1b22", "--findall"])
        self.assertEqual(code, 0)
        self.assertEqual(out.split(), ["1", "22"])

    def test_no_match_exits_one(self) -> None:
        code, out, _ = self.run_cli(["zzz", "--text", "abc"])
        self.assertEqual(code, 1)
        self.assertIn("no match", out)

    def test_bad_pattern_exits_two_with_a_caret(self) -> None:
        code, _, err = self.run_cli(["(a"])
        self.assertEqual(code, 2)
        self.assertIn("missing ')'", err)
        self.assertIn("^", err)

    def test_budget_failure_exits_two(self) -> None:
        code, _, err = self.run_cli(["(a+)+b", "--text", "a" * 20, "--budget", "1000"])
        self.assertEqual(code, 2)
        self.assertIn("exceeded the budget", err)

    def test_explain_and_disasm_modes(self) -> None:
        code, out, _ = self.run_cli(["a|b", "--explain"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().splitlines()[0], "ALT")
        code, out, _ = self.run_cli(["a", "--disasm"])
        self.assertEqual(code, 0)
        self.assertIn("match", out)

    def test_replacement_mode(self) -> None:
        code, out, _ = self.run_cli([r"\d+", "--text", "a1b22", "--replace", "#"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "a#b#")

    def test_escape_mode(self) -> None:
        code, out, _ = self.run_cli(["1+1", "--escape"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "1\\+1")

    def test_flags_reach_the_engine(self) -> None:
        code, out, _ = self.run_cli(["ada", "--text", "ADA", "--ignore-case"])
        self.assertEqual(code, 0)
        self.assertIn("'ADA'", out)


if __name__ == "__main__":
    unittest.main()
