"""Cursor pagination over a list that keeps changing while you read it.

Offset pagination (`?page=3`) is a promise the server cannot keep: insert one row at
the top and every later page is shifted by one, so the reader skips a row without any
error. This module replaces the offset with two things a cursor can carry honestly:

* the **position** — the last ``(sort_key, id)`` the reader saw, compared as a tuple
  so equal sort keys still have a total order;
* the **snapshot** — the store's sequence number when the walk started, so rows
  inserted afterwards are invisible for the rest of the walk.

The cursor is signed and versioned. Clients *will* edit query strings, and a cursor
that cannot be validated is a cursor that can be used to read another tenant's rows.

    >>> store = RowStore()
    >>> _ = store.add(Row("b", 100, "March"))
    >>> _ = store.add(Row("a", 100, "March"))
    >>> _ = store.add(Row("c", 101, "April"))

    >>> page = store.page(limit=2)
    >>> [row.id for row in page.rows]
    ['a', 'b']
    >>> page.has_more
    True

    >>> last = store.page(cursor=page.next_cursor, limit=2)
    >>> [row.id for row in last.rows], last.next_cursor
    (['c'], None)

Equal sort keys are ordered by id, so the walk is stable:

    >>> sorted((row.sort_key, row.id) for row in page.rows)
    [(100, 'a'), (100, 'b')]

A row inserted mid-walk stays out of the walk — that is the snapshot:

    >>> first = store.page(limit=1)
    >>> _ = store.add(Row("0", 100, "late arrival"))
    >>> [row.id for row in store.page(cursor=first.next_cursor, limit=10).rows]
    ['b', 'c']

Cursors are signed, so an edited one is refused:

    >>> codec = CursorCodec(b"server-secret")
    >>> token = codec.encode((100, "b"), snapshot=3)
    >>> codec.decode(token) == ((100, "b"), 3)
    True
    >>> try:
    ...     codec.decode(token[:-2] + "aa")
    ... except InvalidCursor as error:
    ...     print(error)
    signature does not match

A different secret (or a rotated one) refuses old cursors:

    >>> try:
    ...     CursorCodec(b"other-secret").decode(token)
    ... except InvalidCursor as error:
    ...     print(error)
    signature does not match
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Iterator, Sequence

__all__ = [
    "CursorCodec",
    "CursorError",
    "InvalidCursor",
    "Page",
    "Row",
    "RowStore",
    "walk",
]

CURSOR_VERSION = 1


class CursorError(ValueError):
    """Base class for a cursor the server refuses to honour."""


class InvalidCursor(CursorError):
    """The cursor was tampered with, truncated, or produced by another secret."""


@dataclass(frozen=True)
class Row:
    """One row: a stable id, the key it sorts by, and an opaque payload."""

    id: str
    sort_key: int
    payload: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("a row needs a non-empty id")
        if not isinstance(self.sort_key, int) or isinstance(self.sort_key, bool):
            raise ValueError(f"{self.id}: sort_key must be an int")

    @property
    def position(self) -> tuple[int, str]:
        """The cursor position for this row — the total order the walk relies on."""
        return (self.sort_key, self.id)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (binascii.Error, ValueError) as error:
        raise InvalidCursor("the cursor is not valid base64url") from error


class CursorCodec:
    """Encode and decode signed cursors: ``base64url(json).hmac``."""

    def __init__(self, secret: bytes, version: int = CURSOR_VERSION) -> None:
        if not secret:
            raise CursorError("the cursor secret must not be empty")
        self._secret = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
        self.version = version

    def encode(self, position: tuple[int, str], snapshot: int) -> str:
        """Sign a position and a snapshot into an opaque, URL-safe token."""
        payable = json.dumps(
            {"v": self.version, "s": snapshot, "k": position[0], "i": position[1]},
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return f"{_b64encode(payable)}.{self._sign(payable)}"

    def decode(self, token: str) -> tuple[tuple[int, str], int]:
        """Verify and unpack a token, or raise :class:`InvalidCursor`."""
        if not isinstance(token, str) or token.count(".") != 1:
            raise InvalidCursor("the cursor is malformed")
        encoded, signature = token.split(".", 1)
        payable = _b64decode(encoded)
        if not hmac.compare_digest(signature, self._sign(payable)):
            raise InvalidCursor("signature does not match")

        try:
            payload = json.loads(payable)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise InvalidCursor("the cursor payload is not JSON") from error

        if payload.get("v") != self.version:
            raise InvalidCursor(f"unsupported cursor version: {payload.get('v')!r}")
        sort_key, row_id, snapshot = payload.get("k"), payload.get("i"), payload.get("s")
        if not isinstance(sort_key, int) or not isinstance(snapshot, int):
            raise InvalidCursor("the cursor payload has a bad shape")
        if not isinstance(row_id, str) or not row_id:
            raise InvalidCursor("the cursor payload has a bad shape")
        return (sort_key, row_id), snapshot

    def _sign(self, payable: bytes) -> str:
        return hmac.new(self._secret, payable, hashlib.sha256).hexdigest()[:32]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CursorCodec(version={self.version})"


@dataclass(frozen=True)
class Page:
    """One page of rows plus the cursor to continue from (``None`` at the end)."""

    rows: tuple[Row, ...]
    next_cursor: str | None = None
    snapshot: int = 0

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(row.id for row in self.rows)


class RowStore:
    """A mutable row store that paginates by keyset + snapshot."""

    def __init__(self, secret: bytes = b"row-store-secret", max_page: int = 100) -> None:
        if max_page < 1:
            raise ValueError("max_page must be >= 1")
        self.codec = CursorCodec(secret)
        self.max_page = max_page
        self._rows: dict[str, tuple[Row, int]] = {}
        self._sequence = 0

    # -- mutation -------------------------------------------------------- #
    def add(self, row: Row) -> Row:
        """Insert a row; the sequence number is what snapshots are made of."""
        if row.id in self._rows:
            raise ValueError(f"duplicate row id: {row.id}")
        self._sequence += 1
        self._rows[row.id] = (row, self._sequence)
        return row

    def delete(self, row_id: str) -> bool:
        return self._rows.pop(row_id, None) is not None

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def sequence(self) -> int:
        return self._sequence

    # -- reading --------------------------------------------------------- #
    def page(self, cursor: str | None = None, limit: int = 10) -> Page:
        """Return up to ``limit`` rows after ``cursor``, inside its snapshot."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be an integer >= 1")
        if limit > self.max_page:
            raise ValueError(f"limit {limit} exceeds the maximum page size {self.max_page}")

        if cursor is None:
            snapshot = self._sequence
            after: tuple[int, str] | None = None
        else:
            after, snapshot = self.codec.decode(cursor)

        visible = [
            row
            for row, sequence in self._rows.values()
            if sequence <= snapshot and (after is None or row.position > after)
        ]
        visible.sort(key=lambda row: row.position)

        window = visible[: limit + 1]
        has_more = len(window) > limit
        window = window[:limit]
        next_cursor = self.codec.encode(window[-1].position, snapshot) if has_more and window else None
        return Page(rows=tuple(window), next_cursor=next_cursor, snapshot=snapshot)

    def walk(self, limit: int = 10, cursor: str | None = None) -> Iterator[Page]:
        """Yield pages until the walk is exhausted; each page shares one snapshot."""
        seen = set()
        while True:
            if cursor is not None:
                if cursor in seen:  # pragma: no cover - defensive
                    raise CursorError("the same cursor came back twice")
                seen.add(cursor)
            page = self.page(cursor=cursor, limit=limit)
            if not page.rows and not page.has_more:
                return
            yield page
            if page.next_cursor is None:
                return
            cursor = page.next_cursor


def walk(store: RowStore, limit: int = 10) -> Iterator[Page]:
    """Module-level convenience: ``walk(store, 100)``."""
    return store.walk(limit=limit)


def rows_from(payload: object) -> list[Row]:
    """Build rows from decoded JSON: a list of objects, or an object with ``rows``."""
    if isinstance(payload, dict):
        payload = payload.get("rows", [])
    if not isinstance(payload, list):
        raise ValueError("rows must be a JSON list or an object with a 'rows' list")
    rows: list[Row] = []
    for position, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"row #{position}: expected an object")
        try:
            rows.append(
                Row(
                    id=str(entry["id"]),
                    sort_key=int(entry["sort_key"]),
                    payload=str(entry.get("payload", "")),
                )
            )
        except KeyError as error:
            raise ValueError(f"row #{position}: missing {error.args[0]}") from error
    return rows


def gather(pages: Sequence[Page]) -> list[Row]:
    """Flatten pages into rows — used by the tests and the CLI."""
    return [row for page in pages for row in page.rows]
