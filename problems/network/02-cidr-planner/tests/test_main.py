"""Tests for CIDR arithmetic: parsing, host maths, relations, plans, summarising."""
from __future__ import annotations

import io
import ipaddress
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import (
    Conflict,
    IPv4Network,
    Plan,
    from_text,
    plan_subnets,
    summarize,
    to_text,
)


class TestParsing(unittest.TestCase):
    def test_round_trip(self) -> None:
        for text in ("0.0.0.0/0", "10.0.0.0/8", "192.168.1.0/24", "203.0.113.7/32"):
            with self.subTest(text=text):
                self.assertEqual(str(IPv4Network.parse(text)), text)

    def test_host_bits_are_rejected_by_default(self) -> None:
        with self.assertRaises(ValueError):
            IPv4Network.parse("10.0.0.5/24")
        self.assertEqual(str(IPv4Network.parse("10.0.0.5/24", strict=False)), "10.0.0.0/24")

    def test_invalid_input(self) -> None:
        for text in (
            "10.0.0.0",  # no prefix
            "10.0.0.0/33",  # prefix too large
            "10.0.0.256/24",  # bad octet
            "10.0.0/24",  # three octets
            "10.0.0.0.0/24",  # five octets
            "010.0.0.0/24",  # leading zero
            "10.0.0.a/24",  # not a number
            "10.0.0.0/x",  # prefix not a number
            "2001:db8::/32",  # IPv6
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    IPv4Network.parse(text)

    def test_address_helpers(self) -> None:
        self.assertEqual(from_text("255.255.255.255"), 2**32 - 1)
        self.assertEqual(to_text(0), "0.0.0.0")
        with self.assertRaises(ValueError):
            from_text("10.0.0")
        with self.assertRaises(ValueError):
            to_text(-1)

    def test_networks_are_hashable_and_ordered(self) -> None:
        first = IPv4Network.parse("10.0.0.0/24")
        second = IPv4Network.parse("10.0.1.0/24")
        self.assertEqual({first, IPv4Network.parse("10.0.0.0/24")}, {first})
        self.assertLess(first, second)


class TestHostMaths(unittest.TestCase):
    def test_slash_24(self) -> None:
        net = IPv4Network.parse("192.168.1.0/24")
        self.assertEqual(net.network_text, "192.168.1.0")
        self.assertEqual(net.broadcast_text, "192.168.1.255")
        self.assertEqual(net.netmask, "255.255.255.0")
        self.assertEqual(net.wildcard, "0.0.0.255")
        self.assertEqual(net.host_range, ("192.168.1.1", "192.168.1.254"))
        self.assertEqual(net.usable_hosts, 254)
        self.assertEqual(net.size, 256)

    def test_point_to_point_and_host_prefixes(self) -> None:
        self.assertEqual(IPv4Network.parse("10.0.0.0/31").usable_hosts, 2)
        self.assertEqual(IPv4Network.parse("10.0.0.0/31").host_range, ("10.0.0.0", "10.0.0.1"))
        self.assertEqual(IPv4Network.parse("10.0.0.0/32").usable_hosts, 1)
        self.assertEqual(IPv4Network.parse("10.0.0.0/32").host_range, ("10.0.0.0", "10.0.0.0"))
        self.assertEqual(IPv4Network.parse("10.0.0.0/30").usable_hosts, 2)

    def test_default_route(self) -> None:
        net = IPv4Network.parse("0.0.0.0/0")
        self.assertEqual(net.size, 2**32)
        self.assertEqual(net.broadcast_text, "255.255.255.255")
        self.assertTrue(net.contains(IPv4Network.parse("203.0.113.0/24")))

    def test_matches_the_stdlib(self) -> None:
        for text in ("10.0.0.0/8", "172.16.0.0/12", "192.168.5.0/26", "0.0.0.0/0", "10.1.2.3/32"):
            with self.subTest(text=text):
                mine = IPv4Network.parse(text)
                theirs = ipaddress.IPv4Network(text)
                self.assertEqual(mine.network_text, str(theirs.network_address))
                self.assertEqual(mine.broadcast_text, str(theirs.broadcast_address))
                self.assertEqual(mine.netmask, str(theirs.netmask))
                self.assertEqual(mine.usable_hosts, theirs.num_addresses if theirs.prefixlen >= 31 else theirs.num_addresses - 2)


class TestRelations(unittest.TestCase):
    def test_contains(self) -> None:
        base = IPv4Network.parse("10.0.0.0/16")
        self.assertTrue(base.contains(IPv4Network.parse("10.0.1.0/24")))
        self.assertTrue(base.contains(IPv4Network.parse("10.0.0.0/16")))
        self.assertFalse(base.contains(IPv4Network.parse("10.1.0.0/24")))
        self.assertFalse(base.contains(IPv4Network.parse("10.0.0.0/8")))

    def test_contains_via_in_operator(self) -> None:
        self.assertIn(IPv4Network.parse("10.0.0.128/25"), IPv4Network.parse("10.0.0.0/24"))
        self.assertNotIn("10.0.0.0/24", IPv4Network.parse("10.0.0.0/24"))

    def test_overlaps(self) -> None:
        small = IPv4Network.parse("10.0.0.128/25")
        big = IPv4Network.parse("10.0.0.0/24")
        self.assertTrue(small.overlaps(big))
        self.assertTrue(big.overlaps(small))
        self.assertFalse(
            IPv4Network.parse("10.0.0.0/25").overlaps(IPv4Network.parse("10.0.0.128/25"))
        )

    def test_supernet(self) -> None:
        self.assertEqual(str(IPv4Network.parse("10.0.1.0/24").supernet()), "10.0.0.0/23")
        self.assertEqual(str(IPv4Network.parse("10.0.1.0/24").supernet(8)), "10.0.0.0/8")
        with self.assertRaises(ValueError):
            IPv4Network.parse("10.0.1.0/24").supernet(24)
        with self.assertRaises(ValueError):
            IPv4Network.parse("10.0.0.0/8").supernet(9)

    def test_is_private(self) -> None:
        for text in ("10.1.2.0/24", "172.16.5.0/24", "192.168.0.0/16", "127.0.0.1/32", "169.254.0.0/16"):
            with self.subTest(text=text):
                self.assertTrue(IPv4Network.parse(text).is_private)
        for text in ("8.8.8.0/24", "203.0.113.0/24", "172.32.0.0/24"):
            with self.subTest(text=text):
                self.assertFalse(IPv4Network.parse(text).is_private)


class TestDivision(unittest.TestCase):
    def test_subnets(self) -> None:
        base = IPv4Network.parse("10.0.0.0/24")
        quarters = base.subnets(26)
        self.assertEqual(
            [str(net) for net in quarters],
            ["10.0.0.0/26", "10.0.0.64/26", "10.0.0.128/26", "10.0.0.192/26"],
        )
        self.assertEqual(sum(net.size for net in quarters), base.size)

    def test_split_power_of_two(self) -> None:
        subnets = IPv4Network.parse("10.0.0.0/16").split(4)
        self.assertEqual([str(net) for net in subnets], ["10.0.0.0/18", "10.0.64.0/18", "10.0.128.0/18", "10.0.192.0/18"])
        self.assertEqual(IPv4Network.parse("10.0.0.0/24").split(1), [IPv4Network.parse("10.0.0.0/24")])

    def test_split_validation(self) -> None:
        for count in (0, -2, 3, 6, 512):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    IPv4Network.parse("10.0.0.0/24").split(count)

    def test_subnets_validation(self) -> None:
        base = IPv4Network.parse("10.0.0.0/24")
        for target in (24, 20, 33):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    base.subnets(target)


class TestPlanSubnets(unittest.TestCase):
    def setUp(self) -> None:
        self.base = IPv4Network.parse("10.0.0.0/16")

    def test_clean_plan(self) -> None:
        plan = plan_subnets(self.base, ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/28"])
        self.assertTrue(plan.ok)
        self.assertEqual(len(plan.allocations), 3)
        self.assertEqual(plan.total_addresses, 256 + 256 + 16)

    def test_order_is_preserved(self) -> None:
        requests = ["10.0.3.0/28", "10.0.1.0/24", "10.0.2.0/24"]
        self.assertEqual([str(net) for net in plan_subnets(self.base, requests).allocations], requests)

    def test_conflicts_are_detected(self) -> None:
        plan = plan_subnets(self.base, ["10.0.1.0/24", "10.0.1.128/25", "10.0.2.0/24"])
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.conflicts), 1)
        conflict = plan.conflicts[0]
        self.assertEqual(str(conflict.first), "10.0.1.0/24")
        self.assertEqual(str(conflict.second), "10.0.1.128/25")
        self.assertIn("overlaps", str(conflict))

    def test_same_allocation_twice_conflicts(self) -> None:
        plan = plan_subnets(self.base, ["10.0.1.0/24", "10.0.1.0/24"])
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.conflicts), 1)

    def test_allocation_outside_the_base(self) -> None:
        with self.assertRaises(ValueError):
            plan_subnets(self.base, ["192.168.0.0/24"])

    def test_plan_rendering(self) -> None:
        text = str(plan_subnets(self.base, ["10.0.1.0/24", "10.0.1.128/25"]))
        self.assertIn("conflicts:", text)
        self.assertIn("no conflicts", str(plan_subnets(self.base, ["10.0.1.0/24"])))

    def test_plan_dataclass(self) -> None:
        plan = Plan(allocations=(IPv4Network.parse("10.0.0.0/24"),), conflicts=())
        self.assertTrue(plan.ok)
        self.assertEqual(plan.total_addresses, 256)
        self.assertEqual(Conflict(plan.allocations[0], plan.allocations[0]).__class__, Conflict)


class TestSummarize(unittest.TestCase):
    def test_merges_siblings(self) -> None:
        merged = summarize([IPv4Network.parse("10.0.0.128/25"), IPv4Network.parse("10.0.0.0/25")])
        self.assertEqual([str(net) for net in merged], ["10.0.0.0/24"])

    def test_merges_cascades(self) -> None:
        merged = summarize([IPv4Network.parse(f"10.0.0.{offset}/26") for offset in (0, 64, 128, 192)])
        self.assertEqual([str(net) for net in merged], ["10.0.0.0/24"])

    def test_drops_contained_networks(self) -> None:
        merged = summarize([IPv4Network.parse("10.0.0.0/16"), IPv4Network.parse("10.0.5.0/24")])
        self.assertEqual([str(net) for net in merged], ["10.0.0.0/16"])

    def test_keeps_non_contiguous(self) -> None:
        merged = summarize([IPv4Network.parse("10.0.0.0/24"), IPv4Network.parse("10.0.2.0/24")])
        self.assertEqual([str(net) for net in merged], ["10.0.0.0/24", "10.0.2.0/24"])

    def test_does_not_merge_across_prefix_lengths(self) -> None:
        merged = summarize([IPv4Network.parse("10.0.0.0/25"), IPv4Network.parse("10.0.0.128/26")])
        self.assertEqual([str(net) for net in merged], ["10.0.0.0/25", "10.0.0.128/26"])

    def test_empty_and_duplicates(self) -> None:
        self.assertEqual(summarize([]), [])
        self.assertEqual(
            [str(net) for net in summarize([IPv4Network.parse("10.0.0.0/24")] * 3)],
            ["10.0.0.0/24"],
        )

    def test_matches_stdlib_collapse(self) -> None:
        networks = [
            ipaddress.IPv4Network("10.0.0.0/25"),
            ipaddress.IPv4Network("10.0.0.128/25"),
            ipaddress.IPv4Network("10.0.1.0/24"),
            ipaddress.IPv4Network("10.0.4.0/24"),
        ]
        mine = [str(net) for net in summarize(IPv4Network.parse(str(net)) for net in networks)]
        theirs = [str(net) for net in ipaddress.collapse_addresses(networks)]
        self.assertEqual(sorted(mine), sorted(theirs))


class TestCli(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_report(self) -> None:
        code, output = self.run_cli(["10.0.0.0/24"])
        self.assertEqual(code, 0)
        self.assertIn("usable hosts 254", output)
        self.assertIn("private      True", output)

    def test_split(self) -> None:
        code, output = self.run_cli(["10.0.0.0/16", "--split", "4"])
        self.assertEqual(code, 0)
        self.assertIn("4 subnet(s):", output)
        self.assertIn("10.0.192.0/18", output)

    def test_check_with_conflicts_exits_non_zero(self) -> None:
        code, output = self.run_cli(["10.0.0.0/16", "--check", "10.0.1.0/24", "10.0.1.128/25"])
        self.assertEqual(code, 1)
        self.assertIn("overlaps", output)

    def test_check_clean_plan(self) -> None:
        code, output = self.run_cli(["10.0.0.0/16", "--check", "10.0.1.0/24", "10.0.2.0/24"])
        self.assertEqual(code, 0)
        self.assertIn("no conflicts", output)

    def test_summarize_output(self) -> None:
        code, output = self.run_cli(
            [
                "10.0.0.0/16",
                "--check",
                "10.0.0.0/25",
                "10.0.0.128/25",
                "--summarize",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("summarised: 10.0.0.0/24", output)

    def test_errors(self) -> None:
        self.assertEqual(self.run_cli(["10.0.0.0/33"])[0], 1)
        self.assertEqual(self.run_cli(["10.0.0.0/24", "--split", "3"])[0], 1)
        self.assertEqual(self.run_cli(["10.0.0.0/24", "--check", "10.0.9.0/24"])[0], 1)


if __name__ == "__main__":
    unittest.main()
