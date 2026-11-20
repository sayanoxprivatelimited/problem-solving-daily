"""An incremental HTTP/1.1 request parser for a byte stream.

TCP hands you arbitrary pieces, so the parser is a small state machine over a
buffer: feed bytes in, get `None` while the message is incomplete, get a `Request`
the moment it is complete, and keep any extra bytes for the next pipelined request.

Security is part of the grammar here — request smuggling exists because parsers
disagree about the details:

* duplicate `Content-Length` or `Content-Length` + `Transfer-Encoding` → 400
* obs-fold (a header line starting with whitespace) → 400
* bare LF line endings → 400 (strict CRLF only)
* unknown transfer coding → 501, unsupported version → 505

Example
-------
>>> parser = HttpRequestParser()
>>> parser.feed(b"GET /health HTTP/1.1\\r\\nHost: api.example\\r\\n") is None
True
>>> request = parser.feed(b"Accept: */*\\r\\n\\r\\n")
>>> request.method, request.target, request.version
('GET', '/health', 'HTTP/1.1')
>>> request.header("HOST")
'api.example'
>>> request.keep_alive
True
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

CRLF = b"\r\n"
HEADER_TERMINATOR = b"\r\n\r\n"

TOKEN_PATTERN = re.compile(rb"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
METHOD_PATTERN = re.compile(rb"^[A-Z]+$")
SUPPORTED_VERSIONS = {b"HTTP/1.1", b"HTTP/1.0"}

DEFAULT_MAX_HEADER_BYTES = 8 * 1024
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_MAX_HEADERS = 100


class ParseError(Exception):
    """A malformed or oversized request. `status` is the HTTP status to answer with."""

    def __init__(self, reason: str, status: int = 400) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class Request:
    """A parsed request. Header names are lower-cased; lookup is case-insensitive."""

    method: str
    target: str
    version: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name.lower(), default)

    @property
    def keep_alive(self) -> bool:
        connection = (self.header("connection") or "").lower()
        if "close" in connection:
            return False
        if self.version == "HTTP/1.0":
            return "keep-alive" in connection
        return True

    @property
    def is_chunked(self) -> bool:
        return (self.header("transfer-encoding") or "").lower() == "chunked"


class HttpRequestParser:
    """Feed it bytes; it yields complete requests or `None` while it waits."""

    def __init__(
        self,
        *,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_headers: int = DEFAULT_MAX_HEADERS,
    ) -> None:
        if max_header_bytes < 1 or max_body_bytes < 0 or max_headers < 1:
            raise ValueError("limits must be positive")
        self._buffer = bytearray()
        self._max_header_bytes = max_header_bytes
        self._max_body_bytes = max_body_bytes
        self._max_headers = max_headers
        self._failed = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> Request | None:
        """Consume `data`; return a `Request` when one is complete."""
        if self._failed:
            raise ParseError("parser is in a failed state; close the connection", 400)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("feed() expects bytes")

        self._buffer.extend(data)
        try:
            request = self._parse()
        except ParseError:
            self._failed = True
            raise
        return request

    # ------------------------------------------------------------------- internals
    def _parse(self) -> Request | None:
        index = self._buffer.find(HEADER_TERMINATOR)
        if index == -1:
            if b"\n\n" in self._buffer or _has_bare_lf(self._buffer):
                raise ParseError("line endings must be CRLF", 400)
            if len(self._buffer) > self._max_header_bytes:
                raise ParseError("header section too large", 431)
            return None

        header_bytes = bytes(self._buffer[:index])
        if len(header_bytes) > self._max_header_bytes:
            raise ParseError("header section too large", 431)

        lines = header_bytes.split(CRLF)
        request_line = lines[0]
        method, target, version = self._parse_request_line(request_line)
        headers, content_length, te_chunked = self._parse_headers(lines[1:])

        body_start = index + len(HEADER_TERMINATOR)
        if te_chunked:
            decoded = self._decode_chunked(bytes(self._buffer[body_start:]))
            if decoded is None:
                return None
            body, consumed_body = decoded
            consumed = body_start + consumed_body
        elif content_length is not None:
            if len(self._buffer) < body_start + content_length:
                return None
            body = bytes(self._buffer[body_start : body_start + content_length])
            consumed = body_start + content_length
        else:
            body = b""
            consumed = body_start

        del self._buffer[:consumed]
        return Request(
            method=method,
            target=target,
            version=version,
            headers=headers,
            body=body,
        )

    def _parse_request_line(self, line: bytes) -> tuple[str, str, str]:
        parts = line.split(b" ")
        if len(parts) != 3:
            raise ParseError("request line must be 'METHOD target VERSION'", 400)

        method_bytes, target_bytes, version_bytes = parts
        if not METHOD_PATTERN.match(method_bytes):
            raise ParseError("method must be uppercase letters", 400)
        if not target_bytes or b"\x00" in target_bytes:
            raise ParseError("empty or invalid request target", 400)
        if version_bytes not in SUPPORTED_VERSIONS:
            raise ParseError(f"unsupported HTTP version {version_bytes!r}", 505)

        return (
            method_bytes.decode("ascii"),
            target_bytes.decode("latin-1"),
            version_bytes.decode("ascii"),
        )

    def _parse_headers(
        self, lines: list[bytes]
    ) -> tuple[dict[str, str], int | None, bool]:
        headers: dict[str, str] = {}
        content_length: int | None = None
        content_length_seen = False
        transfer_encoding: str | None = None

        if len(lines) > self._max_headers:
            raise ParseError("too many header fields", 431)

        for line in lines:
            if not line:
                raise ParseError("empty header line before end of headers", 400)
            if line[:1] in (b" ", b"\t"):
                raise ParseError("obs-fold header continuation is not allowed", 400)

            name, separator, value = line.partition(b":")
            if not separator:
                raise ParseError("header line missing ':'", 400)
            if not TOKEN_PATTERN.match(name):
                raise ParseError(f"invalid header name {name!r}", 400)

            name_text = name.decode("ascii").lower()
            value_text = value.decode("latin-1").strip()

            if name_text == "content-length":
                if content_length_seen:
                    raise ParseError("duplicate Content-Length header", 400)
                content_length_seen = True
                if not value_text.isdigit():
                    raise ParseError(f"invalid Content-Length {value_text!r}", 400)
                content_length = int(value_text)
                if content_length > self._max_body_bytes:
                    raise ParseError("body too large", 413)
            elif name_text == "transfer-encoding":
                transfer_encoding = value_text

            if name_text in headers:
                headers[name_text] = f"{headers[name_text]}, {value_text}"
            else:
                headers[name_text] = value_text

        te_chunked = False
        if transfer_encoding is not None:
            if content_length_seen:
                raise ParseError("both Content-Length and Transfer-Encoding given", 400)
            if transfer_encoding.lower() != "chunked":
                raise ParseError(f"unsupported transfer coding {transfer_encoding!r}", 501)
            te_chunked = True

        return headers, content_length, te_chunked

    def _decode_chunked(self, data: bytes) -> tuple[bytes, int] | None:
        """Decode a chunked body. Returns (body, bytes_consumed) or None if incomplete."""
        body = bytearray()
        position = 0
        while True:
            line_end = data.find(CRLF, position)
            if line_end == -1:
                return None
            size_field = data[position:line_end].split(b";")[0].strip()
            try:
                size = int(size_field, 16)
            except ValueError as exc:
                raise ParseError(f"invalid chunk size {size_field!r}", 400) from exc
            if size < 0:
                raise ParseError("negative chunk size", 400)
            if len(body) + size > self._max_body_bytes:
                raise ParseError("body too large", 413)

            position = line_end + len(CRLF)
            if size == 0:
                # Discard any trailer fields, then require the final empty line.
                while True:
                    trailer_end = data.find(CRLF, position)
                    if trailer_end == -1:
                        return None
                    if trailer_end == position:
                        return bytes(body), trailer_end + len(CRLF)
                    position = trailer_end + len(CRLF)

            if len(data) < position + size + len(CRLF):
                return None
            body.extend(data[position : position + size])
            terminator = data[position + size : position + size + len(CRLF)]
            if terminator != CRLF:
                raise ParseError("chunk data must be followed by CRLF", 400)
            position += size + len(CRLF)


def _has_bare_lf(data: bytes) -> bool:
    """True when a LF appears without a preceding CR (a smuggling vector)."""
    for index, byte in enumerate(data):
        if byte == 0x0A and (index == 0 or data[index - 1] != 0x0D):
            return True
    return False
