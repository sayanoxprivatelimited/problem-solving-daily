"""Tests for the diff engine: edit scripts, hunk headers, newlines, application."""
from __future__ import annotations

import io
import os
import random
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import (
    NO_NEWLINE,
    PatchError,
    apply_patch,
    diff,
    parse_patch,
    summarize,
)


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def body(patch: str) -> list[str]:
    """The body lines of the first hunk, for readable assertions."""
    lines = patch.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("@@"))
    return lines[start + 1 :]


class TestDiffShape(unittest.TestCase):
    def test_identical_files_produce_nothing(self) -> None:
        self.assertEqual(diff("a\nb\n", "a\nb\n"), "")
        self.assertEqual(diff("", ""), "")

    def test_append(self) -> None:
        patch = diff("one\ntwo\n", "one\ntwo\nthree\n")
        self.assertEqual(
            patch,
            "--- a\n+++ b\n@@ -1,2 +1,3 @@\n one\n two\n+three\n",
        )

    def test_prepend(self) -> None:
        patch = diff("b\nc\n", "a\nb\nc\n")
        self.assertEqual(
            patch,
            "--- a\n+++ b\n@@ -1,2 +1,3 @@\n+a\n b\n c\n",
        )

    def test_delete_everything(self) -> None:
        patch = diff("a\nb\n", "")
        self.assertEqual(patch, "--- a\n+++ b\n@@ -1,2 +0,0 @@\n-a\n-b\n")

    def test_insert_into_an_empty_file(self) -> None:
        patch = diff("", "hello\n")
        self.assertEqual(patch, "--- a\n+++ b\n@@ -0,0 +1 @@\n+hello\n")

    def test_replacement(self) -> None:
        patch = diff("a\nb\nc\n", "a\nB\nc\n", context=1)
        self.assertEqual(
            patch,
            "--- a\n+++ b\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n",
        )

    def test_names_from_the_caller(self) -> None:
        patch = diff("a\n", "b\n", from_name="old.txt", to_name="new.txt")
        self.assertTrue(patch.startswith("--- old.txt\n+++ new.txt\n"))

    def test_the_diff_is_deterministic(self) -> None:
        left = "".join(f"line {index}\n" for index in range(40))
        right = "".join(f"line {index}\n" for index in range(0, 40, 3))
        self.assertEqual(diff(left, right), diff(left, right))

    def test_a_single_line_hunk_omits_the_count(self) -> None:
        patch = diff("a\nb\nc\n", "a\nb\nc\nd\n", context=0)
        self.assertIn("@@ -3,0 +4 @@", patch)


class TestHunks(unittest.TestCase):
    def test_distant_changes_become_two_hunks(self) -> None:
        old = "".join(f"line {index}\n" for index in range(20))
        new = old.replace("line 1\n", "LINE 1\n").replace("line 18\n", "LINE 18\n")
        patch = diff(old, new)
        self.assertEqual(patch.count("@@ -"), 2)
        self.assertEqual(len(parse_patch(patch)), 2)

    def test_nearby_changes_are_merged_into_one_hunk(self) -> None:
        patch = diff("a\nb\nc\nd\ne\nf\ng\n", "a\nB\nc\nd\ne\nF\ng\n")
        self.assertEqual(patch.count("@@ -"), 1)

    def test_context_zero_touches_only_changed_lines(self) -> None:
        patch = diff("a\nb\nc\n", "a\nB\nc\n", context=0)
        self.assertEqual(body(patch), ["-b", "+B"])

    def test_context_is_clamped_at_the_edges(self) -> None:
        patch = diff("a\n", "b\n", context=5)
        self.assertEqual(body(patch), ["-a", "+b"])

    def test_hunk_ranges_are_consistent_with_their_bodies(self) -> None:
        old = "".join(f"{index}\n" for index in range(30))
        new = old.replace("3\n", "").replace("25\n", "twenty five\n")
        for hunk in parse_patch(diff(old, new)).hunks:
            self.assertEqual(len(hunk.old_lines), hunk.old_length)
            self.assertEqual(len(hunk.new_lines), hunk.new_length)

    def test_negative_context_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            diff("a\n", "b\n", context=-1)


class TestNewlines(unittest.TestCase):
    def test_a_missing_final_newline_is_marked_on_both_sides(self) -> None:
        patch = diff("a\nb", "a\nc")
        self.assertEqual(
            patch,
            "--- a\n+++ b\n@@ -1,2 +1,2 @@\n a\n-b\n"
            + NO_NEWLINE
            + "\n+c\n"
            + NO_NEWLINE
            + "\n",
        )
        self.assertEqual(apply_patch("a\nb", patch), "a\nc")

    def test_adding_the_missing_newline_is_a_real_change(self) -> None:
        patch = diff("a\nb", "a\nb\n")
        self.assertIn("+", patch)
        self.assertEqual(apply_patch("a\nb", patch), "a\nb\n")

    def test_crlf_endings_survive(self) -> None:
        old = "a\r\nb\r\n"
        new = "a\r\nB\r\n"
        self.assertEqual(apply_patch(old, diff(old, new)), new)


class TestApply(unittest.TestCase):
    def test_empty_patch_is_identity(self) -> None:
        self.assertEqual(apply_patch("a\nb\n", ""), "a\nb\n")

    def test_round_trip_cases(self) -> None:
        cases = [
            ("", ""),
            ("", "x\n"),
            ("x\n", ""),
            ("a\nb\nc\n", "a\nc\n"),
            ("a\nb\nc\n", "x\na\nb\nc\ny\n"),
            ("one\ntwo\nthree\nfour\nfive\n", "one\n2\nthree\n4\nfive\n"),
            ("a\nb\nc", "a\nb\nc\nd"),
            ("\n\n\n", "\n"),
        ]
        for old, new in cases:
            with self.subTest(old=old, new=new):
                self.assertEqual(apply_patch(old, diff(old, new)), new)

    def test_fuzz_round_trip(self) -> None:
        generator = random.Random(2024)
        alphabet = ["a\n", "b\n", "c\n", "a\n", "d\n"]
        for _ in range(150):
            old = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 12)))
            new = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 12)))
            if generator.random() < 0.3:
                old, new = old.rstrip("\n"), new.rstrip("\n")
            context = generator.randint(0, 4)
            with self.subTest(old=old, new=new, context=context):
                self.assertEqual(apply_patch(old, diff(old, new, context=context)), new)

    def test_a_drifted_file_still_applies(self) -> None:
        patch = diff("a\nb\nc\n", "a\nB\nc\n", context=1)
        shifted = "header 1\nheader 2\na\nb\nc\n"
        self.assertEqual(apply_patch(shifted, patch), "header 1\nheader 2\na\nB\nc\n")

    def test_two_drifted_hunks_keep_their_alignment(self) -> None:
        old = "".join(f"{index}\n" for index in range(20))
        new = old.replace("2\n", "two\n").replace("15\n", "fifteen\n")
        patch = diff(old, new)
        shifted = "prefix\nprefix\n" + old
        expected = "prefix\nprefix\n" + new
        self.assertEqual(apply_patch(shifted, patch), expected)

    def test_a_wrong_file_is_refused(self) -> None:
        patch = diff("a\nb\n", "a\nz\n")
        with self.assertRaises(PatchError) as caught:
            apply_patch("totally\ndifferent\n", patch)
        self.assertIn("hunk 1 does not apply", str(caught.exception))

    def test_nothing_is_returned_when_a_later_hunk_fails(self) -> None:
        old = "".join(f"{index}\n" for index in range(20))
        new = old.replace("2\n", "two\n").replace("15\n", "fifteen\n")
        patch = diff(old, new)
        broken = old.replace("15\n", "fifteen?\n")
        with self.assertRaises(PatchError):
            apply_patch(broken, patch)

    def test_malformed_patches_are_refused(self) -> None:
        for bad in (
            "no header here\n",
            "@@ -1,2 +1,2 @@\n+a\n",
            "@@ -1 +1 @@\n-a\n-b\n",
            "@@ -1,1 +1,1 @@\n~a\n",
            "@@ -x,y +1,1 @@\n",
        ):
            with self.subTest(patch=bad):
                with self.assertRaises(PatchError):
                    apply_patch("a\n", bad)

    def test_an_overlapping_hunk_is_refused(self) -> None:
        patch = (
            "--- a\n+++ b\n"
            "@@ -1,2 +1,2 @@\n-a\n+A\n b\n"
            "@@ -1,1 +1,0 @@\n-a\n"
        )
        with self.assertRaises(PatchError) as caught:
            apply_patch("a\nb\nc\n", patch)
        self.assertIn("overlaps", str(caught.exception))

    def test_the_original_string_is_not_mutated(self) -> None:
        original = "a\nb\n"
        apply_patch(original, diff(original, "a\nc\n"))
        self.assertEqual(original, "a\nb\n")


class TestPerformance(unittest.TestCase):
    def test_a_few_edits_in_a_big_file_are_cheap(self) -> None:
        old = "".join(f"line {index}\n" for index in range(4000))
        new = old.replace("line 10\n", "LINE 10\n").replace("line 3990\n", "LINE 3990\n")
        started = time.perf_counter()
        patch = diff(old, new)
        elapsed = time.perf_counter() - started
        self.assertEqual(patch.count("@@ -"), 2)
        self.assertLess(elapsed, 2.0, f"diffing took {elapsed:.2f}s")
        self.assertEqual(apply_patch(old, patch), new)


class TestSummarize(unittest.TestCase):
    def test_counts_added_and_removed(self) -> None:
        patch = diff("a\nb\nc\n", "a\nB\nc\nd\n")
        self.assertEqual(summarize(patch), "1 hunk(s), +2 -1")

    def test_empty_patch(self) -> None:
        self.assertEqual(summarize(""), "0 hunk(s), +0 -0")


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.old = os.path.join(self.tmp, "old.txt")
        self.new = os.path.join(self.tmp, "new.txt")
        self.patch = os.path.join(self.tmp, "change.patch")
        with open(self.old, "w", encoding="utf-8") as handle:
            handle.write("a\nb\nc\n")
        with open(self.new, "w", encoding="utf-8") as handle:
            handle.write("a\nB\nc\nd\n")

    def test_diff_prints_a_patch(self) -> None:
        code, out, _ = run_cli("diff", self.old, self.new)
        self.assertEqual(code, 0)
        self.assertIn("@@ -1,3 +1,4 @@", out)
        self.assertIn("+B", out)

    def test_diff_stat_goes_to_stderr(self) -> None:
        code, out, err = run_cli("diff", self.old, self.new, "--stat", "--context", "1")
        self.assertEqual(code, 0)
        self.assertIn("1 hunk(s), +2 -1", err)
        self.assertNotIn("hunk(s)", out)

    def test_identical_files_print_nothing(self) -> None:
        code, out, _ = run_cli("diff", self.old, self.old)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_write_patch_then_apply_it(self) -> None:
        code, _, _ = run_cli("diff", self.old, self.new, "-o", self.patch)
        self.assertEqual(code, 0)
        code, out, _ = run_cli("apply", self.patch, self.old)
        self.assertEqual(code, 0)
        self.assertEqual(out, "a\nB\nc\nd\n")

    def test_apply_to_a_file(self) -> None:
        run_cli("diff", self.old, self.new, "-o", self.patch)
        target = os.path.join(self.tmp, "out.txt")
        code, _, _ = run_cli("apply", self.patch, self.old, "-o", target)
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "a\nB\nc\nd\n")

    def test_a_bad_patch_exits_one(self) -> None:
        with open(self.patch, "w", encoding="utf-8") as handle:
            handle.write("@@ -1 +1 @@\n-zzz\n+yyy\n")
        code, _, err = run_cli("apply", self.patch, self.old)
        self.assertEqual(code, 1)
        self.assertIn("does not apply", err)

    def test_missing_files_exit_two(self) -> None:
        code, _, err = run_cli("diff", os.path.join(self.tmp, "nope"), self.new)
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
