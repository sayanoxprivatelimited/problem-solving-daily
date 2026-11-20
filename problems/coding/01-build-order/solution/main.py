"""Build every package after the packages it depends on — and say why when it cannot.

Three decisions make this more useful than a textbook topological sort:

* **Cycles are reported as a path** (``api -> db -> common -> api``). "Cycle detected"
  costs an afternoon; a path is a five-minute fix.
* **Ties break alphabetically** via a heap, so the same manifest always produces the
  same log, the same cache key and the same test expectation.
* **The rebuild set is a graph query, not a filter.** `changed` is closed over reverse
  edges, because changing `common` means rebuilding everything that consumes it.

Example
-------
>>> packages = parse_manifest('{"a": {"version": "1"}, "b": {"version": "1", "depends": ["a"]}}')
>>> build_order(packages)
['a', 'b']
>>> find_cycle(parse_manifest('{"a": {"depends": ["b"]}, "b": {"depends": ["a"]}}'))
['a', 'b', 'a']
"""
from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


class DependencyError(Exception):
    """The graph cannot be built: a cycle or a missing dependency."""


@dataclass(frozen=True)
class Package:
    """One package and the packages it needs."""

    name: str
    version: str = "0.0.0"
    depends: tuple[str, ...] = ()


def parse_manifest(text: str) -> dict[str, Package]:
    """Parse a JSON manifest of `{name: {version, depends}}` into `Package` objects."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object of package name -> config")

    packages: dict[str, Package] = {}
    for name, config in payload.items():
        if not name or not isinstance(name, str):
            raise ValueError(f"invalid package name: {name!r}")
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValueError(f"package {name!r} must map to an object")

        version = config.get("version", "0.0.0")
        if not isinstance(version, str):
            raise ValueError(f"package {name!r}: version must be a string")

        depends = config.get("depends", [])
        if isinstance(depends, str):
            raise ValueError(f"package {name!r}: depends must be a list, not a string")
        if not isinstance(depends, list) or not all(isinstance(item, str) for item in depends):
            raise ValueError(f"package {name!r}: depends must be a list of names")

        packages[name] = Package(
            name=name, version=version, depends=tuple(dict.fromkeys(depends))
        )

    return packages


def build_order(
    packages: Mapping[str, Package],
    *,
    installed: Iterable[str] = (),
    changed: Iterable[str] | None = None,
) -> list[str]:
    """Return the packages to build, in dependency order.

    `installed` packages are skipped, `changed` restricts the build to those packages
    and everything that (transitively) depends on them.
    """
    installed_set = set(installed)
    targets = _rebuild_targets(packages, changed, installed_set)

    remaining = {
        name: {dependency for dependency in packages[name].depends if dependency in targets}
        for name in targets
    }
    ready = [name for name, dependencies in remaining.items() if not dependencies]
    heapq.heapify(ready)

    order: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        for candidate, dependencies in remaining.items():
            if name in dependencies:
                dependencies.discard(name)
                if not dependencies:
                    heapq.heappush(ready, candidate)

    if len(order) != len(targets):
        cycle = find_cycle(packages, targets=targets)
        raise DependencyError("cycle detected: " + " -> ".join(cycle))

    return order


def find_cycle(
    packages: Mapping[str, Package], *, targets: Iterable[str] | None = None
) -> list[str]:
    """Return one dependency cycle as a path that starts and ends at the same node."""
    allowed = set(targets) if targets is not None else set(packages)
    visiting: list[str] = []
    done: set[str] = set()

    def walk(name: str) -> list[str] | None:
        if name in visiting:
            return visiting[visiting.index(name) :] + [name]
        if name in done:
            return None
        visiting.append(name)
        for dependency in packages[name].depends:
            if dependency not in allowed:
                continue
            cycle = walk(dependency)
            if cycle is not None:
                return cycle
        visiting.pop()
        done.add(name)
        return None

    for name in sorted(allowed):
        cycle = walk(name)
        if cycle is not None:
            return cycle
    return []


def _rebuild_targets(
    packages: Mapping[str, Package],
    changed: Iterable[str] | None,
    installed: set[str],
) -> set[str]:
    """Validate the dependency graph and compute the set of packages to build."""
    for name, package in packages.items():
        for dependency in package.depends:
            if dependency not in packages:
                raise DependencyError(
                    f"package {name!r} depends on unknown package {dependency!r}"
                )

    if changed is None:
        return set(packages) - installed

    changed_set = set(changed)
    unknown = changed_set - set(packages)
    if unknown:
        raise DependencyError("changed packages not in manifest: " + ", ".join(sorted(unknown)))

    affected = set(changed_set)
    frontier = list(changed_set)
    while frontier:
        current = frontier.pop()
        for name, package in packages.items():
            if current in package.depends and name not in affected:
                affected.add(name)
                frontier.append(name)

    return affected - (installed - changed_set)


def render(packages: Mapping[str, Package], order: Sequence[str]) -> str:
    """Human-readable build plan."""
    if not order:
        return "nothing to build — everything is installed and up to date"
    lines = [f"build order ({len(order)} package(s)):"]
    lines += [
        f"  {index:>2}. {name} {packages[name].version}".rstrip()
        for index, name in enumerate(order, start=1)
    ]
    return "\n".join(lines)
