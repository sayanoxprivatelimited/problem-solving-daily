"""Tests for the build order: graph order, cycles, incremental rebuilds, CLI."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from solution import cli
from solution.main import (
    DependencyError,
    Package,
    build_order,
    find_cycle,
    parse_manifest,
    render,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Package]:
    return parse_manifest((FIXTURES / name).read_text(encoding="utf-8"))


class TestParseManifest(unittest.TestCase):
    def test_typical_manifest(self) -> None:
        packages = parse_manifest(
            '{"a": {"version": "1.0"}, "b": {"version": "2.0", "depends": ["a"]}}'
        )
        self.assertEqual(packages["a"], Package(name="a", version="1.0", depends=()))
        self.assertEqual(packages["b"].depends, ("a",))

    def test_defaults(self) -> None:
        packages = parse_manifest('{"a": {}}')
        self.assertEqual(packages["a"].version, "0.0.0")
        self.assertEqual(packages["a"].depends, ())
        self.assertEqual(parse_manifest('{"a": null}')["a"].depends, ())

    def test_duplicate_dependencies_are_collapsed(self) -> None:
        packages = parse_manifest('{"a": {"depends": ["b", "b", "c"]}, "b": {}, "c": {}}')
        self.assertEqual(packages["a"].depends, ("b", "c"))

    def test_invalid_manifests(self) -> None:
        for text in (
            "not json",
            "[]",
            '{"a": "not an object"}',
            '{"a": {"version": 1.0}}',
            '{"a": {"depends": "b"}}',
            '{"a": {"depends": [1, 2]}}',
            '{"": {}}',
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_manifest(text)


class TestBuildOrder(unittest.TestCase):
    def setUp(self) -> None:
        self.packages = load("packages.json")

    def test_dependencies_come_first(self) -> None:
        order = build_order(self.packages)
        position = {name: index for index, name in enumerate(order)}
        for name, package in self.packages.items():
            for dependency in package.depends:
                self.assertLess(position[dependency], position[name], f"{dependency} before {name}")

    def test_full_order_is_deterministic(self) -> None:
        self.assertEqual(
            build_order(self.packages),
            ["common", "logging", "db", "auth", "api", "web", "worker"],
        )
        self.assertEqual(build_order(self.packages), build_order(self.packages))

    def test_independent_packages_are_alphabetical(self) -> None:
        packages = parse_manifest('{"c": {}, "a": {}, "b": {}}')
        self.assertEqual(build_order(packages), ["a", "b", "c"])

    def test_diamond(self) -> None:
        packages = parse_manifest(
            '{"d": {"depends": ["b", "c"]}, "b": {"depends": ["a"]},'
            ' "c": {"depends": ["a"]}, "a": {}}'
        )
        order = build_order(packages)
        self.assertEqual(order[0], "a")
        self.assertEqual(order[-1], "d")
        self.assertEqual(sorted(order[1:3]), ["b", "c"])

    def test_empty_manifest(self) -> None:
        self.assertEqual(build_order({}), [])

    def test_duplicate_free_output(self) -> None:
        order = build_order(self.packages)
        self.assertEqual(len(order), len(set(order)))

    def test_single_package(self) -> None:
        self.assertEqual(build_order(parse_manifest('{"only": {}}')), ["only"])


class TestCycles(unittest.TestCase):
    def test_cycle_path_is_reported(self) -> None:
        packages = load("cycle.json")
        with self.assertRaises(DependencyError) as caught:
            build_order(packages)
        message = str(caught.exception)
        self.assertIn("cycle detected", message)
        self.assertIn("api -> db -> common -> api", message)

    def test_find_cycle_path(self) -> None:
        self.assertEqual(find_cycle(load("cycle.json")), ["api", "db", "common", "api"])

    def test_self_dependency_is_a_cycle(self) -> None:
        packages = parse_manifest('{"a": {"depends": ["a"]}}')
        with self.assertRaises(DependencyError) as caught:
            build_order(packages)
        self.assertIn("a -> a", str(caught.exception))

    def test_two_node_cycle(self) -> None:
        packages = parse_manifest('{"a": {"depends": ["b"]}, "b": {"depends": ["a"]}}')
        self.assertEqual(find_cycle(packages), ["a", "b", "a"])

    def test_acyclic_graph_has_no_cycle(self) -> None:
        self.assertEqual(find_cycle(load("packages.json")), [])

    def test_cycle_inside_a_subset(self) -> None:
        packages = parse_manifest(
            '{"ok": {}, "a": {"depends": ["b"]}, "b": {"depends": ["a"]}}'
        )
        self.assertEqual(find_cycle(packages, targets=["a", "b"]), ["a", "b", "a"])
        self.assertEqual(find_cycle(packages, targets=["ok"]), [])


class TestUnknownDependencies(unittest.TestCase):
    def test_missing_dependency_names_both_packages(self) -> None:
        packages = parse_manifest('{"api": {"depends": ["ghost"]}}')
        with self.assertRaises(DependencyError) as caught:
            build_order(packages)
        message = str(caught.exception)
        self.assertIn("api", message)
        self.assertIn("ghost", message)

    def test_unknown_changed_package(self) -> None:
        packages = load("packages.json")
        with self.assertRaises(DependencyError) as caught:
            build_order(packages, changed=["nope"])
        self.assertIn("nope", str(caught.exception))


class TestIncrementalBuilds(unittest.TestCase):
    def setUp(self) -> None:
        self.packages = load("packages.json")

    def test_installed_packages_are_skipped(self) -> None:
        order = build_order(self.packages, installed=["common", "logging", "db"])
        self.assertEqual(order, ["auth", "api", "web", "worker"])

    def test_everything_installed_builds_nothing(self) -> None:
        self.assertEqual(build_order(self.packages, installed=list(self.packages)), [])

    def test_changed_rebuilds_dependents_transitively(self) -> None:
        self.assertEqual(
            build_order(self.packages, changed=["db"]),
            ["db", "auth", "api", "web", "worker"],
        )

    def test_changed_leaf_rebuilds_only_itself(self) -> None:
        self.assertEqual(build_order(self.packages, changed=["web"]), ["web"])

    def test_changed_root_rebuilds_everything(self) -> None:
        self.assertEqual(build_order(self.packages, changed=["common"]), build_order(self.packages))

    def test_changed_respects_installed(self) -> None:
        order = build_order(self.packages, installed=["common", "logging"], changed=["db"])
        self.assertEqual(order, ["db", "auth", "api", "web", "worker"])

    def test_changed_already_installed_is_still_rebuilt(self) -> None:
        order = build_order(self.packages, installed=["db"], changed=["db"])
        self.assertEqual(order[0], "db")

    def test_changed_empty_list_builds_nothing(self) -> None:
        self.assertEqual(build_order(self.packages, changed=[]), [])

    def test_changed_and_installed_combination_is_consistent(self) -> None:
        order = build_order(self.packages, installed=["common"], changed=["logging"])
        self.assertEqual(
            order, ["logging", "db", "auth", "api", "web", "worker"]
        )


class TestRenderAndCli(unittest.TestCase):
    def test_render_empty(self) -> None:
        self.assertIn("nothing to build", render({}, []))

    def test_render_lists_numbered_packages(self) -> None:
        packages = load("packages.json")
        text = render(packages, ["common", "logging"])
        self.assertIn("1. common 2.0.0", text)
        self.assertIn("2. logging 1.1.0", text)

    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_cli_full_build(self) -> None:
        code, output = self.run_cli([str(FIXTURES / "packages.json")])
        self.assertEqual(code, 0)
        self.assertIn("build order (7 package(s))", output)
        self.assertIn("7. worker 0.9.0", output)

    def test_cli_changed(self) -> None:
        code, output = self.run_cli([str(FIXTURES / "packages.json"), "--changed", "db"])
        self.assertEqual(code, 0)
        self.assertIn("build order (5 package(s))", output)
        self.assertIn("(changed: db)", output)

    def test_cli_installed(self) -> None:
        code, output = self.run_cli(
            [str(FIXTURES / "packages.json"), "--installed", "common", "logging", "db"]
        )
        self.assertEqual(code, 0)
        self.assertIn("build order (4 package(s))", output)

    def test_cli_cycle_error(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cli.main([str(FIXTURES / "cycle.json")])
        self.assertEqual(code, 1)
        self.assertIn("cycle detected", out.getvalue())

    def test_cli_missing_file(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(FIXTURES / "nope.json")]), 1)

    def test_cli_invalid_manifest(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
