"""Tests for the evaluation harness: micro scores, cost, and the gate that decides."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import (
    EvaluationError,
    Example,
    Gate,
    Score,
    Suite,
    Verdict,
    compare,
    evaluate,
    load_cases,
    load_json_file,
)

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class TestExample(unittest.TestCase):
    def test_rejects_an_empty_case_id(self) -> None:
        with self.assertRaises(EvaluationError):
            Example("  ", {"ok"}, {"ok"})

    def test_rejects_a_negative_cost(self) -> None:
        with self.assertRaises(EvaluationError):
            Example("a", {"ok"}, {"ok"}, cost_usd=-0.01)

    def test_rejects_a_non_numeric_cost(self) -> None:
        with self.assertRaises(EvaluationError):
            Example("a", {"ok"}, {"ok"}, cost_usd="free")  # type: ignore[arg-type]

    def test_a_bare_string_is_one_label(self) -> None:
        example = Example("a", "ok", "ok")
        self.assertEqual(example.expected, frozenset({"ok"}))

    def test_rejects_an_empty_label(self) -> None:
        with self.assertRaises(EvaluationError):
            Example("a", {"ok", " "}, {"ok"})

    def test_is_immutable(self) -> None:
        example = Example("a", {"ok"}, {"ok"})
        with self.assertRaises(Exception):
            example.cost_usd = 1.0  # type: ignore[misc]

    def test_label_differences(self) -> None:
        example = Example("a", {"ok", "urgent"}, {"ok", "billing"})
        self.assertEqual(example.true_positives, 1)
        self.assertEqual(example.false_positives, 1)
        self.assertEqual(example.false_negatives, 1)
        self.assertFalse(example.is_exact)


class TestEvaluate(unittest.TestCase):
    def test_an_empty_suite_is_an_error(self) -> None:
        with self.assertRaises(EvaluationError):
            evaluate([])

    def test_duplicate_case_ids_are_an_error(self) -> None:
        with self.assertRaises(EvaluationError) as caught:
            evaluate([Example("a", {"ok"}, {"ok"}), Example("a", {"ok"}, {"ok"})])
        self.assertIn("duplicate case id: a", str(caught.exception))

    def test_rejects_a_non_example(self) -> None:
        with self.assertRaises(EvaluationError):
            evaluate([{"id": "a"}])  # type: ignore[list-item]

    def test_a_perfect_run_scores_one_everywhere(self) -> None:
        score = evaluate([Example("a", {"ok"}, {"ok"}), Example("b", {"no"}, {"no"})])
        self.assertEqual((score.tp, score.fp, score.fn), (2, 0, 0))
        self.assertEqual(score.precision, 1.0)
        self.assertEqual(score.recall, 1.0)
        self.assertEqual(score.f1, 1.0)
        self.assertEqual(score.accuracy, 1.0)

    def test_nothing_expected_and_nothing_predicted_is_perfect(self) -> None:
        score = evaluate([Example("a", set(), set())])
        self.assertEqual(score.precision, 1.0)
        self.assertEqual(score.recall, 1.0)
        self.assertEqual(score.accuracy, 1.0)

    def test_micro_averaging_counts_labels_not_cases(self) -> None:
        score = evaluate(
            [
                Example("a", {"ok", "urgent"}, {"ok"}),  # 1 tp, 1 fn
                Example("b", {"ok"}, {"ok", "billing"}),  # 1 tp, 1 fp
            ]
        )
        self.assertEqual((score.tp, score.fp, score.fn), (2, 1, 1))
        self.assertAlmostEqual(score.precision, 2 / 3)
        self.assertAlmostEqual(score.recall, 2 / 3)
        self.assertAlmostEqual(score.accuracy, 0.0)

    def test_exact_match_is_stricter_than_f1(self) -> None:
        score = evaluate([Example("a", {"ok", "urgent"}, {"ok"})] * 1)
        self.assertEqual(score.accuracy, 0.0)
        self.assertAlmostEqual(score.precision, 1.0)
        self.assertAlmostEqual(score.recall, 0.5)

    def test_cost_is_normalised_per_thousand_cases(self) -> None:
        score = evaluate(
            [Example(str(index), {"ok"}, {"ok"}, cost_usd=0.0015) for index in range(4)]
        )
        self.assertAlmostEqual(score.total_cost_usd, 0.006)
        self.assertAlmostEqual(score.cost_per_1k, 1.5)

    def test_supported_counts_every_label_decision(self) -> None:
        score = evaluate([Example("a", {"ok", "urgent"}, {"ok", "billing", "spam"})])
        self.assertEqual(score.supported, 4)

    def test_as_dict_is_rounded_and_stable(self) -> None:
        payload = evaluate([Example("a", {"ok"}, {"ok"}, cost_usd=0.0001234)]).as_dict()
        self.assertEqual(payload["cases"], 1)
        self.assertEqual(payload["precision"], 1.0)
        self.assertEqual(payload["cost_per_1k"], 0.1234)


class TestSuite(unittest.TestCase):
    def test_suite_builds_up_and_scores(self) -> None:
        suite = Suite("triage").add(Example("a", {"ok"}, {"ok"})).extend([Example("b", {"x"}, {"x"})])
        self.assertEqual(len(suite), 2)
        self.assertEqual(suite.score().accuracy, 1.0)

    def test_a_suite_needs_a_name(self) -> None:
        with self.assertRaises(EvaluationError):
            Suite("  ")

    def test_an_empty_suite_cannot_be_scored(self) -> None:
        with self.assertRaises(EvaluationError):
            Suite("empty").score()


class TestGateValidation(unittest.TestCase):
    def test_precision_floor_must_be_a_fraction(self) -> None:
        with self.assertRaises(EvaluationError):
            Gate(min_precision=1.5)

    def test_tolerances_cannot_be_negative(self) -> None:
        with self.assertRaises(EvaluationError):
            Gate(max_drop=-0.1)
        with self.assertRaises(EvaluationError):
            Gate(max_cost_increase=-1)

    def test_budget_cannot_be_negative(self) -> None:
        with self.assertRaises(EvaluationError):
            Gate(max_cost_per_1k=-0.01)


class TestGateCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = evaluate(load_json_file(os.path.join(FIXTURES, "baseline.json")))
        self.candidate = evaluate(load_json_file(os.path.join(FIXTURES, "candidate.json")))

    def test_the_fixtures_have_the_numbers_the_brief_promises(self) -> None:
        self.assertAlmostEqual(self.baseline.precision, 1.0)
        self.assertAlmostEqual(self.baseline.recall, 4 / 6)
        self.assertAlmostEqual(self.baseline.cost_per_1k, 0.4)
        self.assertAlmostEqual(self.candidate.precision, 4 / 5)
        self.assertAlmostEqual(self.candidate.recall, 4 / 6)
        self.assertAlmostEqual(self.candidate.cost_per_1k, 1.1)

    def test_floors_pass_and_fail(self) -> None:
        self.assertTrue(Gate(min_precision=0.8, min_recall=0.6).check(self.candidate).passed)
        failures = Gate(min_precision=0.9, min_recall=0.8).check(self.candidate).failures
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0], "precision 0.800 is below the floor 0.900")
        self.assertEqual(failures[1], "recall 0.667 is below the floor 0.800")

    def test_cost_budget(self) -> None:
        self.assertTrue(Gate(max_cost_per_1k=2.0).check(self.candidate).passed)
        failures = Gate(max_cost_per_1k=0.5).check(self.candidate).failures
        self.assertEqual(failures, ("cost per 1k is 1.100, over the budget 0.500",))

    def test_precision_regression_is_caught(self) -> None:
        failures = Gate(max_drop=0.01).check(self.candidate, baseline=self.baseline).failures
        self.assertTrue(any("precision regressed from 1.000 to 0.800" in line for line in failures))
        # recall is unchanged, so it must not be reported
        self.assertFalse(any("recall regressed" in line for line in failures))

    def test_the_tolerance_is_respected(self) -> None:
        verdict = Gate(max_drop=0.25, max_cost_increase=2.0).check(
            self.candidate, baseline=self.baseline
        )
        self.assertTrue(verdict.passed, verdict.failures)

    def test_cost_regression_is_caught_with_its_percentage(self) -> None:
        verdict = Gate().check(self.candidate, baseline=self.baseline)
        self.assertTrue(any("allowed increase 0.0%" in line for line in verdict.failures))
        self.assertTrue(any("cost per 1k regressed from 0.400 to 1.100" in line for line in verdict.failures))

    def test_every_failure_is_reported_in_a_stable_order(self) -> None:
        gate = Gate(min_precision=0.9, min_recall=0.9, max_cost_per_1k=0.1)
        first = gate.check(self.candidate, baseline=self.baseline).failures
        second = gate.check(self.candidate, baseline=self.baseline).failures
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)

    def test_checked_names_describe_what_ran(self) -> None:
        verdict = Gate(min_precision=0.1).check(self.candidate, baseline=self.baseline)
        self.assertEqual(
            verdict.checked,
            ("precision floor", "precision regression", "recall regression", "cost regression"),
        )

    def test_verdict_summary_and_render(self) -> None:
        failing = Gate(min_precision=0.99).check(self.candidate)
        self.assertEqual(failing.summary, "fail — 1 problem(s)")
        self.assertIn("✗ precision 0.800", failing.render())
        passing = Gate(min_precision=0.1).check(self.candidate)
        self.assertEqual(passing.summary, "pass — 1 check(s)")
        self.assertEqual(passing.render(), "pass — 1 check(s)")

    def test_a_hand_built_score_works_the_same_way(self) -> None:
        perfect = Score(cases=1, tp=1, fp=0, fn=0, accuracy=1.0, total_cost_usd=0.0)
        self.assertTrue(Gate(min_precision=1.0).check(perfect).passed)
        self.assertIsInstance(Gate().check(perfect), Verdict)


class TestLoadCases(unittest.TestCase):
    def test_accepts_a_bare_list_or_a_wrapper(self) -> None:
        self.assertEqual(len(load_cases([{"id": "a"}])), 1)
        self.assertEqual(len(load_cases({"cases": [{"id": "a"}, {"id": "b"}]})), 2)
        self.assertEqual(load_cases({"cases": []}), [])

    def test_missing_fields_are_defaulted(self) -> None:
        example = load_cases([{"id": "a"}])[0]
        self.assertEqual(example.expected, frozenset())
        self.assertEqual(example.predicted, frozenset())
        self.assertEqual(example.cost_usd, 0.0)

    def test_bad_shapes_are_explained(self) -> None:
        with self.assertRaises(EvaluationError):
            load_cases("nope")
        with self.assertRaises(EvaluationError) as caught:
            load_cases([["a"]])
        self.assertIn("case #0: expected an object", str(caught.exception))
        with self.assertRaises(EvaluationError) as caught:
            load_cases([{"id": "a", "cost_usd": "much"}])
        self.assertIn("case a:", str(caught.exception))

    def test_missing_and_invalid_files(self) -> None:
        with self.assertRaises(EvaluationError) as caught:
            load_json_file(os.path.join(FIXTURES, "nope.json"))
        self.assertIn("nope.json", str(caught.exception))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
            broken = handle.name
        try:
            with self.assertRaises(EvaluationError) as caught:
                load_json_file(broken)
            self.assertIn("invalid JSON", str(caught.exception))
        finally:
            os.unlink(broken)

    def test_compare_scores_both_sides(self) -> None:
        candidate, verdict = compare(
            load_cases([{"id": "a", "expected": ["ok"], "predicted": ["ok"]}]),
            baseline=load_cases([{"id": "a", "expected": ["ok"], "predicted": []}]),
            gate=Gate(min_recall=0.5),
        )
        self.assertEqual(candidate.recall, 1.0)
        self.assertTrue(verdict.passed)


class TestCli(unittest.TestCase):
    candidate = os.path.join(FIXTURES, "candidate.json")
    baseline = os.path.join(FIXTURES, "baseline.json")

    def test_a_passing_gate_exits_zero(self) -> None:
        code, out, _ = run_cli(self.candidate, "--min-precision", "0.8")
        self.assertEqual(code, 0)
        self.assertIn("candidate  precision 0.800", out)
        self.assertIn("pass —", out)

    def test_a_failing_gate_exits_one(self) -> None:
        code, out, _ = run_cli(self.candidate, "--min-precision", "0.95")
        self.assertEqual(code, 1)
        self.assertIn("fail — 1 problem(s)", out)
        self.assertIn("✗ precision 0.800 is below the floor 0.950", out)

    def test_baseline_comparison_prints_both_rows(self) -> None:
        code, out, _ = run_cli(
            self.candidate, "--baseline", self.baseline, "--max-drop", "0.25",
            "--max-cost-increase", "2.0",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("baseline   precision 1.000", out)

    def test_json_output_has_the_verdict(self) -> None:
        code, out, _ = run_cli(self.candidate, "--json", "--max-cost-per-1k", "0.5")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["passed"])
        self.assertEqual(payload["candidate"]["cases"], 5)
        self.assertEqual(len(payload["failures"]), 1)
        self.assertIsNone(payload["baseline"])

    def test_unusable_input_exits_two(self) -> None:
        code, _, err = run_cli(os.path.join(FIXTURES, "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        code, _, err = run_cli(self.candidate, "--min-precision", "2.0")
        self.assertEqual(code, 2)
        self.assertIn("between 0 and 1", err)

    def test_empty_suite_is_an_input_error_not_a_pass(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"cases": []}, handle)
            empty = handle.name
        try:
            code, _, err = run_cli(empty)
        finally:
            os.unlink(empty)
        self.assertEqual(code, 2)
        self.assertIn("empty suite", err)


if __name__ == "__main__":
    unittest.main()
