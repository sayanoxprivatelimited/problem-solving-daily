"""An evaluation harness that refuses a regression instead of reporting one.

A support classifier is judged by an eval: run the labelled tickets, compute
precision and recall, compare with yesterday, decide whether to deploy. The version
that only prints numbers is a dashboard. The version that fails the build is a gate,
and a gate needs three things this module provides:

* labels as **sets**, micro-averaged, because one ticket can carry two labels;
* money, in the only unit a budget is written in — cost per 1,000 cases;
* a verdict that lists *every* reason a run must not ship, deterministically.

The rules that are easy to get wrong:

1. Micro-averaging counts labels, not cases. A case with two expected labels and one
   predicted contributes one true positive and one false negative.
2. Empty versus empty is a perfect answer. Returning nothing is a real behaviour and
   must score 1.0, not a division-by-zero.
3. Exact-match accuracy is separate from F1. F1 flatters a model that gets half of a
   two-label ticket right every single time.
4. Absolute floors and baseline regressions are different questions. "Good enough to
   ship" is a floor; "not worse than yesterday" is a delta; a gate answers both.

    >>> examples = [
    ...     Example("q1", {"ok"}, {"ok"}),
    ...     Example("q2", {"ok"}, {"ok"}),
    ...     Example("q3", {"bad"}, {"ok"}),
    ... ]
    >>> score = evaluate(examples)
    >>> score.cases, score.tp, score.fp, score.fn
    (3, 2, 1, 1)
    >>> round(score.precision, 3), round(score.recall, 3)
    (0.667, 0.667)
    >>> round(score.accuracy, 3)
    0.667

A gate turns those numbers into a decision:

    >>> Gate(min_precision=0.9).check(score).failures
    ('precision 0.667 is below the floor 0.900',)
    >>> Gate(min_precision=0.5).check(score).passed
    True

Cost is normalised, so two suites of different size are comparable:

    >>> evaluate([Example("a", {"ok"}, {"ok"}, cost_usd=0.002)]).cost_per_1k
    2.0
    >>> round(evaluate([Example(f"{i}", {"ok"}, {"ok"}, cost_usd=0.001) for i in range(4)]).cost_per_1k, 3)
    1.0

Everything is frozen: a score you can mutate is a score two threads disagree about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Sequence

__all__ = [
    "EvaluationError",
    "Example",
    "Gate",
    "Score",
    "Suite",
    "Verdict",
    "evaluate",
    "load_cases",
]


class EvaluationError(ValueError):
    """The suite or the gate itself is wrong — not the model under test."""


def _labels(value: Iterable[str] | str) -> frozenset[str]:
    """Normalise any label collection to a frozenset, treating a string as one label."""
    if isinstance(value, str):
        labels = {value} if value else set()
    else:
        labels = {str(label) for label in value}
    for label in labels:
        if not label.strip():
            raise EvaluationError("a label cannot be empty or whitespace")
    return frozenset(labels)


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    """1.0 when there is nothing to divide: no errors at all is a perfect score."""
    if denominator == 0:
        return 1.0
    return numerator / denominator


@dataclass(frozen=True)
class Example:
    """One evaluated case: what should have been predicted and what was."""

    case_id: str
    expected: frozenset[str]
    predicted: frozenset[str]
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise EvaluationError("case_id must be a non-empty string")
        if not isinstance(self.cost_usd, (int, float)) or isinstance(self.cost_usd, bool):
            raise EvaluationError(f"{self.case_id}: cost_usd must be a number")
        if self.cost_usd < 0:
            raise EvaluationError(f"{self.case_id}: cost_usd must be >= 0")
        object.__setattr__(self, "expected", _labels(self.expected))
        object.__setattr__(self, "predicted", _labels(self.predicted))

    @property
    def is_exact(self) -> bool:
        """Did the model reproduce the label set exactly?"""
        return self.expected == self.predicted

    @property
    def true_positives(self) -> int:
        return len(self.expected & self.predicted)

    @property
    def false_positives(self) -> int:
        return len(self.predicted - self.expected)

    @property
    def false_negatives(self) -> int:
        return len(self.expected - self.predicted)


@dataclass(frozen=True)
class Score:
    """The result of one run. Constructed by :func:`evaluate`, never by hand."""

    cases: int
    tp: int
    fp: int
    fn: int
    accuracy: float
    total_cost_usd: float

    @property
    def precision(self) -> float:
        return _safe_div(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return _safe_div(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    @property
    def cost_per_1k(self) -> float:
        """Cost of answering 1,000 tickets — the unit a budget is written in."""
        if not self.cases:
            return 0.0
        return self.total_cost_usd / self.cases * 1000

    @property
    def supported(self) -> int:
        """How many label decisions the run made (the denominator of micro scores)."""
        return self.tp + self.fp + self.fn

    def as_dict(self) -> dict[str, float | int]:
        """A stable, rounded view for JSON output and logs."""
        return {
            "cases": self.cases,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "f1": round(self.f1, 6),
            "accuracy": round(self.accuracy, 6),
            "cost_per_1k": round(self.cost_per_1k, 6),
        }


def evaluate(examples: Iterable[Example]) -> Score:
    """Score an iterable of cases, micro-averaging labels across all of them.

    An empty suite is an error: a gate that can be satisfied by scoring nothing is
    worse than no gate. Duplicate ids are an error too — they usually mean a fixture
    was appended twice, which silently doubles that case's weight.

        >>> try:
        ...     evaluate([])
        ... except EvaluationError as error:
        ...     print(error)
        cannot evaluate an empty suite
        >>> try:
        ...     evaluate([Example("a", {"ok"}, {"ok"}), Example("a", {"ok"}, {"ok"})])
        ... except EvaluationError as error:
        ...     print(error)
        duplicate case id: a

    Multi-label cases are counted label by label:

        >>> score = evaluate([Example("a", {"ok", "urgent"}, {"ok"})])
        >>> score.tp, score.fp, score.fn
        (1, 0, 1)
        >>> score.accuracy
        0.0
    """
    items = list(examples)
    if not items:
        raise EvaluationError("cannot evaluate an empty suite")

    seen: set[str] = set()
    tp = fp = fn = exact = 0
    total_cost = 0.0

    for example in items:
        if not isinstance(example, Example):
            raise EvaluationError(f"expected an Example, got {type(example).__name__}")
        if example.case_id in seen:
            raise EvaluationError(f"duplicate case id: {example.case_id}")
        seen.add(example.case_id)
        tp += example.true_positives
        fp += example.false_positives
        fn += example.false_negatives
        exact += int(example.is_exact)
        total_cost += example.cost_usd

    return Score(
        cases=len(items),
        tp=tp,
        fp=fp,
        fn=fn,
        accuracy=exact / len(items),
        total_cost_usd=total_cost,
    )


@dataclass
class Suite:
    """A named, growable collection of cases — the thing a gate runs against."""

    name: str
    examples: list[Example] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise EvaluationError("a suite needs a name")

    def add(self, example: Example) -> "Suite":
        """Append one case and return ``self`` so calls can be chained."""
        self.examples.append(example)
        return self

    def extend(self, examples: Iterable[Example]) -> "Suite":
        for example in examples:
            self.add(example)
        return self

    def __len__(self) -> int:
        return len(self.examples)

    def score(self) -> Score:
        return evaluate(self.examples)


@dataclass(frozen=True)
class Verdict:
    """The gate's answer: pass, or the complete list of reasons it failed."""

    passed: bool
    failures: tuple[str, ...] = ()
    checked: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if self.passed:
            return f"pass — {len(self.checked)} check(s)"
        return f"fail — {len(self.failures)} problem(s)"

    def render(self) -> str:
        """A build-log friendly multi-line report."""
        lines = [self.summary]
        for failure in self.failures:
            lines.append(f"  ✗ {failure}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Gate:
    """Absolute floors plus tolerances relative to a baseline.

    ``max_drop`` and ``max_cost_increase`` are tolerances: ``0.0`` means "no
    regression at all", ``0.02`` means "up to two points worse is acceptable noise".
    """

    min_precision: float | None = None
    min_recall: float | None = None
    max_cost_per_1k: float | None = None
    max_drop: float = 0.0
    max_cost_increase: float = 0.0

    def __post_init__(self) -> None:
        for name in ("min_precision", "min_recall"):
            value = getattr(self, name)
            if value is None:
                continue
            if not 0.0 <= float(value) <= 1.0:
                raise EvaluationError(f"{name} must be between 0 and 1, got {value}")
        for name in ("max_drop", "max_cost_increase"):
            value = float(getattr(self, name))
            if value < 0:
                raise EvaluationError(f"{name} must be >= 0, got {value}")
        if self.max_cost_per_1k is not None and float(self.max_cost_per_1k) < 0:
            raise EvaluationError("max_cost_per_1k must be >= 0")

    def check(self, candidate: Score, baseline: Score | None = None) -> Verdict:
        """Compare a run with the floors, and with the baseline when there is one.

            >>> run = evaluate([Example("a", {"ok"}, {"ok"}, cost_usd=0.001)])
            >>> worse = evaluate([Example("a", {"ok"}, set(), cost_usd=0.003)])
            >>> Gate(min_recall=0.9).check(worse).failures
            ('recall 0.000 is below the floor 0.900',)
            >>> Gate(max_cost_per_1k=1.0).check(worse).failures
            ('cost per 1k is 3.000, over the budget 1.000',)
            >>> report = Gate(max_drop=0.01).check(worse, baseline=run)
            >>> report.passed
            False
            >>> len(report.failures)
            2

        A regression inside the tolerance passes, and the reason lists every failure
        in a fixed order so two runs diff cleanly.
        """
        failures: list[str] = []
        checked: list[str] = []

        if self.min_precision is not None:
            checked.append("precision floor")
            if candidate.precision < float(self.min_precision):
                failures.append(
                    f"precision {candidate.precision:.3f} is below the floor "
                    f"{float(self.min_precision):.3f}"
                )

        if self.min_recall is not None:
            checked.append("recall floor")
            if candidate.recall < float(self.min_recall):
                failures.append(
                    f"recall {candidate.recall:.3f} is below the floor "
                    f"{float(self.min_recall):.3f}"
                )

        if self.max_cost_per_1k is not None:
            checked.append("cost budget")
            budget = float(self.max_cost_per_1k)
            if candidate.cost_per_1k > budget:
                failures.append(
                    f"cost per 1k is {candidate.cost_per_1k:.3f}, over the budget {budget:.3f}"
                )

        if baseline is not None:
            tolerance = float(self.max_drop)
            for metric in ("precision", "recall"):
                checked.append(f"{metric} regression")
                before = float(getattr(baseline, metric))
                after = float(getattr(candidate, metric))
                if after < before - tolerance:
                    failures.append(
                        f"{metric} regressed from {before:.3f} to {after:.3f} "
                        f"(drop {before - after:.3f} > allowed {tolerance:.3f})"
                    )

            checked.append("cost regression")
            budget = baseline.cost_per_1k * (1 + float(self.max_cost_increase))
            if candidate.cost_per_1k > budget:
                failures.append(
                    f"cost per 1k regressed from {baseline.cost_per_1k:.3f} to "
                    f"{candidate.cost_per_1k:.3f} (allowed increase "
                    f"{float(self.max_cost_increase) * 100:.1f}%)"
                )

        return Verdict(passed=not failures, failures=tuple(failures), checked=tuple(checked))


def load_cases(payload: object) -> list[Example]:
    """Build cases from decoded JSON: a bare list, or an object with ``cases``.

        >>> load_cases([{"id": "a", "expected": ["ok"], "predicted": "ok"}])[0].predicted
        frozenset({'ok'})
        >>> load_cases({"cases": []})
        []

    Every malformed field is reported with the case id, because a fixture error in a
    CI log is read once and must be enough.
    """
    if isinstance(payload, dict):
        payload = payload.get("cases", [])
    if not isinstance(payload, list):
        raise EvaluationError("cases must be a JSON list or an object with a 'cases' list")

    examples: list[Example] = []
    for position, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise EvaluationError(f"case #{position}: expected an object")
        case_id = str(entry.get("id", "")).strip()
        try:
            examples.append(
                Example(
                    case_id=str(entry["id"]),
                    expected=entry.get("expected", []),
                    predicted=entry.get("predicted", []),
                    cost_usd=float(entry.get("cost_usd", 0.0)),
                )
            )
        except KeyError as error:
            raise EvaluationError(f"case #{position}: missing {error.args[0]}") from error
        except (TypeError, ValueError) as error:
            raise EvaluationError(f"case {case_id or f'#{position}'}: {error}") from error
    return examples


def load_json_file(path: str) -> list[Example]:
    """Read and validate a fixture file produced by a previous run."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as error:
        raise EvaluationError(f"{path}: {error.strerror}") from error
    except json.JSONDecodeError as error:
        raise EvaluationError(f"{path}: invalid JSON ({error.msg})") from error
    return load_cases(payload)


def compare(
    candidate: Sequence[Example], baseline: Sequence[Example] | None = None, gate: Gate | None = None
) -> tuple[Score, Verdict]:
    """One call for the common case: score both sides and run the gate."""
    candidate_score = evaluate(candidate)
    baseline_score = evaluate(baseline) if baseline else None
    verdict = (gate or Gate()).check(candidate_score, baseline=baseline_score)
    return candidate_score, verdict
