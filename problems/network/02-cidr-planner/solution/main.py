"""IPv4 CIDR arithmetic: parse, split, compare, plan, and summarise.

The whole module is integer arithmetic on 32-bit addresses — strings only appear at
the edges. That is what makes `contains` and `overlaps` one-liners instead of a pile
of string comparisons, and it is why `/31` and `/32` fall out naturally instead of
being special cases sprinkled through the code.

Example
-------
>>> net = IPv4Network.parse("10.0.0.0/24")
>>> str(net), net.broadcast_text, net.usable_hosts
('10.0.0.0/24', '10.0.0.255', 254)
>>> str(IPv4Network.parse("10.0.0.128/25")) in [str(n) for n in net.subnets(25)]
True
>>> net.contains(IPv4Network.parse("10.0.0.128/25"))
True
>>> [str(n) for n in IPv4Network.parse("10.0.0.0/30").split(2)]
['10.0.0.0/31', '10.0.0.2/31']
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

BITS = 32
MAX_ADDRESS = (1 << BITS) - 1


def _mask(prefix: int) -> int:
    """Netmask as an integer (0.0.0.0 for /0, 255.255.255.255 for /32)."""
    if prefix <= 0:
        return 0
    return (MAX_ADDRESS << (BITS - prefix)) & MAX_ADDRESS


def to_text(address: int) -> str:
    """Format a 32-bit integer as dotted-quad."""
    if not 0 <= address <= MAX_ADDRESS:
        raise ValueError(f"address out of range: {address}")
    return ".".join(str((address >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def from_text(text: str) -> int:
    """Parse dotted-quad into a 32-bit integer, validating each octet."""
    parts = text.split(".")
    if len(parts) != 4:
        raise ValueError(f"invalid IPv4 address {text!r}: expected four octets")
    value = 0
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part[0] == "0"):
            raise ValueError(f"invalid IPv4 address {text!r}: bad octet {part!r}")
        octet = int(part)
        if octet > 255:
            raise ValueError(f"invalid IPv4 address {text!r}: octet {octet} > 255")
        value = (value << 8) | octet
    return value


@dataclass(frozen=True, order=True)
class IPv4Network:
    """An immutable IPv4 prefix. `address` always has its host bits cleared."""

    address: int
    prefix: int

    def __post_init__(self) -> None:
        if not 0 <= self.address <= MAX_ADDRESS:
            raise ValueError(f"address out of range: {self.address}")
        if not 0 <= self.prefix <= BITS:
            raise ValueError(f"prefix must be 0..32, got {self.prefix}")

    # --------------------------------------------------------------------- parsing
    @classmethod
    def parse(cls, text: str, *, strict: bool = True) -> "IPv4Network":
        """Parse ``a.b.c.d/prefix``. `strict` rejects addresses with host bits set."""
        if ":" in text:
            raise ValueError(f"IPv6 is not supported: {text!r}")
        if "/" not in text:
            raise ValueError(f"missing prefix length: {text!r}")

        address_text, _, prefix_text = text.partition("/")
        if not prefix_text.isdigit():
            raise ValueError(f"invalid prefix length: {prefix_text!r}")
        prefix = int(prefix_text)
        if prefix > BITS:
            raise ValueError(f"prefix must be 0..32, got {prefix}")

        address = from_text(address_text.strip())
        network = cls(address & _mask(prefix), prefix)
        if strict and address != network.address:
            raise ValueError(
                f"{text!r} has host bits set; did you mean {network}? "
                "pass strict=False to normalise"
            )
        return network

    # ------------------------------------------------------------------- formatting
    def __str__(self) -> str:
        return f"{self.network_text}/{self.prefix}"

    @property
    def size(self) -> int:
        """Number of addresses in the prefix (hosts + network + broadcast)."""
        return 1 << (BITS - self.prefix)

    @property
    def network_text(self) -> str:
        return to_text(self.address)

    @property
    def broadcast(self) -> int:
        return self.address + self.size - 1

    @property
    def broadcast_text(self) -> str:
        return to_text(self.broadcast)

    @property
    def netmask(self) -> str:
        return to_text(_mask(self.prefix))

    @property
    def wildcard(self) -> str:
        return to_text(MAX_ADDRESS ^ _mask(self.prefix))

    @property
    def usable_hosts(self) -> int:
        """Host addresses: /32 → 1, /31 → 2 (RFC 3021), otherwise size - 2."""
        if self.prefix >= BITS - 1:
            return self.size
        return self.size - 2

    @property
    def host_range(self) -> tuple[str, str]:
        if self.prefix == BITS:
            return self.network_text, self.network_text
        if self.prefix == BITS - 1:
            return self.network_text, self.broadcast_text
        return to_text(self.address + 1), to_text(self.broadcast - 1)

    @property
    def is_private(self) -> bool:
        """RFC 1918, loopback and link-local — i.e. "not routable on the internet"."""
        for network in _private_networks():
            if network.contains(self):
                return True
        return False

    # ------------------------------------------------------------------- relations
    def contains(self, other: "IPv4Network") -> bool:
        if other.prefix < self.prefix:
            return False
        if self.prefix == 0:
            return True
        shift = BITS - self.prefix
        return (self.address >> shift) == (other.address >> shift)

    def __contains__(self, other: object) -> bool:
        return isinstance(other, IPv4Network) and self.contains(other)

    def overlaps(self, other: "IPv4Network") -> bool:
        return self.contains(other) or other.contains(self)

    def supernet(self, new_prefix: int | None = None) -> "IPv4Network":
        """The enclosing prefix. Defaults to one bit shorter."""
        target = self.prefix - 1 if new_prefix is None else new_prefix
        if not 0 <= target < self.prefix:
            raise ValueError(f"new_prefix must be 0..{self.prefix - 1}, got {target}")
        return IPv4Network(self.address & _mask(target), target)

    # -------------------------------------------------------------------- division
    def subnets(self, new_prefix: int) -> list["IPv4Network"]:
        """All prefixes of `new_prefix` inside this one, in address order."""
        if not self.prefix < new_prefix <= BITS:
            raise ValueError(
                f"new_prefix must be {self.prefix + 1}..{BITS}, got {new_prefix}"
            )
        step = 1 << (BITS - new_prefix)
        return [
            IPv4Network(self.address + offset, new_prefix)
            for offset in range(0, self.size, step)
        ]

    def split(self, count: int) -> list["IPv4Network"]:
        """Split into `count` equal subnets. `count` must be a power of two."""
        if count < 1:
            raise ValueError("count must be >= 1")
        if count & (count - 1):
            raise ValueError(f"count must be a power of two, got {count}")
        if count > self.size:
            raise ValueError(f"cannot split {self} into {count} subnets")
        extra_bits = count.bit_length() - 1
        return [self] if extra_bits == 0 else self.subnets(self.prefix + extra_bits)


@lru_cache(maxsize=1)
def _private_networks() -> tuple[IPv4Network, ...]:
    """RFC 1918 + loopback + link-local, as networks."""
    return (
        IPv4Network.parse("10.0.0.0/8"),
        IPv4Network.parse("172.16.0.0/12"),
        IPv4Network.parse("192.168.0.0/16"),
        IPv4Network.parse("127.0.0.0/8"),
        IPv4Network.parse("169.254.0.0/16"),
    )


@dataclass(frozen=True)
class Conflict:
    """Two allocations that cannot both exist."""

    first: IPv4Network
    second: IPv4Network

    def __str__(self) -> str:
        return f"{self.first} overlaps {self.second}"


@dataclass(frozen=True)
class Plan:
    """Allocations in request order, plus every overlap found between them."""

    allocations: tuple[IPv4Network, ...]
    conflicts: tuple[Conflict, ...]

    @property
    def ok(self) -> bool:
        return not self.conflicts

    @property
    def total_addresses(self) -> int:
        return sum(network.size for network in self.allocations)

    def __str__(self) -> str:
        lines = [f"{str(network):<20} {network.usable_hosts:>10} usable" for network in self.allocations]
        if self.conflicts:
            lines += ["", "conflicts:"] + [f"  {conflict}" for conflict in self.conflicts]
        else:
            lines += ["", "no conflicts"]
        return "\n".join(lines)


def plan_subnets(base: IPv4Network, requests: Sequence[str]) -> Plan:
    """Validate every requested CIDR against `base` and against each other."""
    allocations: list[IPv4Network] = []
    for text in requests:
        network = IPv4Network.parse(text, strict=False)
        if not base.contains(network):
            raise ValueError(f"{network} is outside {base}")
        allocations.append(network)

    conflicts: list[Conflict] = []
    for index, first in enumerate(allocations):
        for second in allocations[index + 1 :]:
            if first.overlaps(second):
                conflicts.append(Conflict(first=first, second=second))

    return Plan(allocations=tuple(allocations), conflicts=tuple(conflicts))


def summarize(networks: Iterable[IPv4Network]) -> list[IPv4Network]:
    """Collapse a set of prefixes into the smallest covering list."""
    ordered = sorted(set(networks))
    if not ordered:
        return []

    # Drop networks already covered by an earlier (shorter or equal) prefix.
    minimal: list[IPv4Network] = []
    for network in ordered:
        if any(existing.contains(network) for existing in minimal):
            continue
        minimal.append(network)

    # Repeatedly merge sibling pairs at the same prefix length.
    changed = True
    while changed:
        changed = False
        merged: list[IPv4Network] = []
        index = 0
        while index < len(minimal):
            current = minimal[index]
            if index + 1 < len(minimal):
                neighbour = minimal[index + 1]
                if (
                    neighbour.prefix == current.prefix
                    and current.broadcast + 1 == neighbour.address
                    and current.prefix > 0
                ):
                    merged.append(current.supernet())
                    index += 2
                    changed = True
                    continue
            merged.append(current)
            index += 1
        minimal = merged

    return minimal
