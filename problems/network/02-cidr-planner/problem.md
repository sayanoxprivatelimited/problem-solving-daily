---
title: CIDR planner for a VPC
category: network
level: medium
concepts: IPv4 math, subnets, overlap detection, address aggregation
status: solved
---

# CIDR planner for a VPC

## Context

You have a `/16` for a VPC and a spreadsheet of "who needs what": a `/24` for the
public subnets, a `/24` for the app tier, a `/28` for the database, a `/28` for the
VPN. Someone will inevitably paste `10.0.1.0/24` next to `10.0.1.128/25`, and someone
else will ask "how many hosts do we really get?".

This is the arithmetic that answers those questions before anything is provisioned:
parse CIDR, compute network/broadcast/usable hosts, detect overlaps, and collapse a
list of allocations into the smallest equivalent set of prefixes.

## Input / Output

```python
base = IPv4Network.parse("10.0.0.0/16")
base.split(4)                       # -> 4 x /18
plan = plan_subnets(base, ["10.0.1.0/24", "10.0.1.128/25", "10.0.2.0/24"])
plan.conflicts                      # -> ((10.0.1.0/24, 10.0.1.128/25),)
summarize([...])                    # -> collapsed list of CIDRs
```

## Requirements

| # | Requirement | Why |
|---|-------------|-----|
| 1 | Parse/format `a.b.c.d/prefix`, validating every octet and the prefix | a typo must not become a subnet |
| 2 | Reject host bits set (`10.0.0.5/24`) unless `strict=False` | "10.0.0.5/24" silently means something else |
| 3 | `usable_hosts`: `/24` → 254, `/31` → 2, `/32` → 1 | RFC 3021 point-to-point links are real |
| 4 | `contains` / `overlaps` by prefix arithmetic, not string comparison | "10.0.1.0/24 contains 10.0.1.128/25" |
| 5 | `split(n)` requires a power of two that fits; `subnets(new_prefix)` validates direction | silent rounding is how ranges get lost |
| 6 | `plan_subnets` keeps request order and reports every conflict pair | humans review in the order they wrote |
| 7 | `summarize` merges sibling prefixes and drops contained ones | firewall rules and route tables want the minimal set |
| 8 | IPv6 input is rejected with a clear message | out of scope, and half-support is worse than none |

## Constraints & acceptance criteria

- `IPv4Network` is immutable and hashable, ordered by `(address, prefix)`.
- `0.0.0.0/0` works and contains everything; `/32` is a single host.
- `is_private` follows RFC 1918 plus loopback and link-local.
- Conflicts are reported as unique unordered pairs, in discovery order.

## Documented limits

- IPv4 only — IPv6 has a different mental model (no broadcast, no "usable hosts").
- The stdlib `ipaddress` module does all of this in production. This implementation
  exists so the arithmetic is visible, and the parity is checked in the tests.

## How to run

```bash
cd problems/network/02-cidr-planner
python -m unittest discover -s tests -t .
python -m solution.cli 10.0.0.0/16 --split 4
python -m solution.cli 10.0.0.0/16 --check 10.0.1.0/24 10.0.1.128/25
```

## Stretch goals

- IPv6 support with the same interface (`/64` arithmetic, no broadcast).
- Emit Terraform `aws_subnet` blocks for a plan.
- Interactive allocator: next free `/24` inside a VPC.
