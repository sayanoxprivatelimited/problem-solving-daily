---
title: Build order from package manifests
category: coding
level: medium
concepts: dependency graphs, topological sort, cycle reporting, incremental builds
status: solved
---

# Build order from package manifests

## Context

A monorepo has 40 packages with a dependency graph nobody fully understands. CI must
build them in an order that never builds a package before its dependencies, must stop
with a *useful* error when someone introduces a cycle, and must not rebuild the world
when one package changes.

"Topological sort" is one line in a textbook. The useful version answers three
questions: **what order**, **which cycle** (as a path, not "cycle detected"), and
**what actually needs rebuilding**.

## Input / Output

```json
{
  "common": {"version": "2.0.0"},
  "db":     {"version": "0.4.1", "depends": ["common"]},
  "api":    {"version": "1.2.0", "depends": ["db", "common"]}
}
```

```python
packages = parse_manifest(text)
build_order(packages)                          # ['common', 'db', 'api']
build_order(packages, changed=["common"])      # ['common', 'db', 'api']
build_order(packages, installed=["common"], changed=["db"])  # ['db', 'api']
```

## Requirements

| # | Requirement | Why |
|---|-------------|-----|
| 1 | Dependencies always come before dependents | a build that races is a flaky CI |
| 2 | Independent packages are ordered alphabetically | reproducible logs and cache keys |
| 3 | A cycle raises `DependencyError` with the **path** (`a -> b -> c -> a`) | "cycle detected" wastes an afternoon |
| 4 | An unknown dependency names both the package and the missing dependency | typos are the most common failure |
| 5 | `installed` packages are not rebuilt | incremental CI |
| 6 | `changed` rebuilds the changed packages **and everything depending on them** (transitively) | this is the actual feature request |
| 7 | A self-dependency is a cycle, not a special case | one code path, one message |
| 8 | Deterministic for identical input, including the error message | tests and humans both need it |

## Constraints & acceptance criteria

- `changed` and `installed` accept package names; unknown names in `changed` raise.
- Packages are only skipped when `installed` **and** not in the rebuild closure.
- The returned order is a list of names with no duplicates.
- `parse_manifest` validates the JSON shape and raises `ValueError` with the offending
  key, because a malformed manifest must not fail 20 minutes into a build.

## Documented limits

- One phase per package: no build/test parallelism windows (a real system would emit a
  DAG of stages instead of a linear list — same algorithm, richer output).
- Versions are read but not resolved: this is a build-order problem, not a package
  manager. Semver constraints are explicitly out of scope.

## How to run

```bash
cd problems/coding/01-build-order
python -m unittest discover -s tests -t .
python -m solution.cli tests/fixtures/packages.json
python -m solution.cli tests/fixtures/packages.json --changed common
```

## Stretch goals

- Emit parallel stages (levels) instead of a flat order.
- Blast-radius report: "changing `common` rebuilds 12 packages, ~40 minutes".
- Read `pyproject.toml` / `package.json` instead of a custom manifest.
