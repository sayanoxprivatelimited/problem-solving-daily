"""A WebSocket (RFC 6455) frame decoder that survives arbitrary chunking.

Frames do not arrive one per ``recv()``. A read can hand you half a header, three
frames, or the first byte of a 70 KB binary payload — so the decoder has to be a state
machine that keeps what it has and waits for the rest. That is requirement one, and it
is the one every "works on localhost" implementation gets wrong.

The other half of the job is refusing bad input *correctly*: the peer must be told
which close code to send.

* `1002` — protocol error: reserved bits, reserved opcodes, a fragmented control frame,
  a non-minimal length, a masked frame when masking is forbidden (or the reverse),
  a continuation with no message open, a new message while one is still open, a close
  code that may not appear on the wire.
* `1007` — invalid UTF-8 in a text message or a close reason.
* `1009` — a message or frame larger than the configured limit.

UTF-8 is validated **after** reassembly, because a single character may straddle two
fragments — validating per frame rejects perfectly valid traffic.

    >>> decoder = Decoder()
    >>> decoder.feed(encode_frame(0x1, b"hello"))
    [Text(text='hello')]

One byte at a time produces the same event, just later:

    >>> decoder = Decoder()
    >>> events = []
    >>> for byte in encode_frame(0x1, b"abc"):
    ...     events.extend(decoder.feed(bytes([byte])))
    >>> events
    [Text(text='abc')]

Client frames are masked, and the mask is applied over the payload only:

    >>> masked = encode_frame(0x1, b"hi", mask=b"\\x37\\xfa\\x21\\x3d")
    >>> Decoder(require_masking=True).feed(masked)
    [Text(text='hi')]

    >>> try:
    ...     Decoder(require_masking=True).feed(encode_frame(0x1, b"hi"))
    ... except ProtocolError as error:
    ...     print(error)
    1002 client frames must be masked

Fragments are reassembled, and control frames may arrive in between:

    >>> decoder = Decoder()
    >>> events = decoder.feed(encode_frame(0x1, b"hel", fin=False))
    >>> events += decoder.feed(encode_frame(0x9, b"hb"))
    >>> events += decoder.feed(encode_frame(0x0, b"lo", fin=True))
    >>> events
    [Ping(data=b'hb'), Text(text='hello')]

Close frames carry a code and a reason, and the codes that may not appear on the wire
are refused:

    >>> Decoder().feed(encode_frame(0x8, b"\\x03\\xe8bye"))
    [Close(code=1000, reason='bye')]
    >>> try:
    ...     Decoder().feed(encode_frame(0x8, b"\\x03\\xee"))
    ... except ProtocolError as error:
    ...     print(error)
    1002 invalid close code 1006
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Union

__all__ = [
    "Binary",
    "Close",
    "Decoder",
    "Event",
    "Ping",
    "Pong",
    "ProtocolError",
    "Text",
    "encode_frame",
    "xor_mask",
]

CONTINUATION = 0x0
TEXT = 0x1
BINARY = 0x2
CLOSE = 0x8
PING = 0x9
PONG = 0xA

DATA_OPCODES = {CONTINUATION, TEXT, BINARY}
CONTROL_OPCODES = {CLOSE, PING, PONG}
RESERVED_OPCODES = {0x3, 0x4, 0x5, 0x6, 0x7, 0xB, 0xC, 0xD, 0xE, 0xF}

# 1005 (no status), 1006 (abnormal closure) and 1015 (TLS) are local-only values:
# they must never be sent, and 1004 is reserved.
FORBIDDEN_CLOSE_CODES = {1004, 1005, 1006, 1015}


class ProtocolError(Exception):
    """A frame the peer must be failed for, with the close code to send."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"{code} {reason}")
        self.code = code
        self.reason = reason


# ------------------------------------------------------------------ events -- #
@dataclass(frozen=True)
class Text:
    text: str

    kind = "text"


@dataclass(frozen=True)
class Binary:
    data: bytes

    kind = "binary"


@dataclass(frozen=True)
class Ping:
    data: bytes

    kind = "ping"


@dataclass(frozen=True)
class Pong:
    data: bytes

    kind = "pong"


@dataclass(frozen=True)
class Close:
    code: int | None = None
    reason: str = ""

    kind = "close"


Event = Union[Text, Binary, Ping, Pong, Close]


# ------------------------------------------------------------------ masking -- #
def xor_mask(payload: bytes, mask: bytes) -> bytes:
    """XOR the payload with the repeating 4-byte mask (the RFC's masking step)."""
    if len(mask) != 4:
        raise ValueError("the masking key is exactly 4 bytes")
    if not payload:
        return b""
    repeated = (mask * (len(payload) // 4 + 1))[: len(payload)]
    return bytes(byte ^ key for byte, key in zip(payload, repeated))


def encode_frame(
    opcode: int, payload: bytes = b"", fin: bool = True, mask: bytes | None = None
) -> bytes:
    """Build one frame — the mirror image of the decoder, and how the tests speak."""
    if not 0 <= opcode <= 0xF:
        raise ValueError("opcode must fit in 4 bits")
    length = len(payload)
    mask_bit = 0x80 if mask is not None else 0
    first = (0x80 if fin else 0x00) | opcode

    if length < 126:
        header = bytes([first, mask_bit | length])
    elif length <= 0xFFFF:
        header = bytes([first, mask_bit | 126]) + struct.pack(">H", length)
    else:
        header = bytes([first, mask_bit | 127]) + struct.pack(">Q", length)

    body = payload
    if mask is not None:
        if len(mask) != 4:
            raise ValueError("the masking key is exactly 4 bytes")
        header += mask
        body = xor_mask(payload, mask)
    return header + body


@dataclass(frozen=True)
class _Raw:
    fin: bool
    opcode: int
    payload: bytes
    masked: bool


# ----------------------------------------------------------------- decoder -- #
DEFAULT_MAX_MESSAGE = 1024 * 1024
DEFAULT_MAX_FRAME = 1024 * 1024


@dataclass
class Decoder:
    """Feed bytes, get events. Never assumes a frame boundary matches a read."""

    require_masking: bool | None = None
    max_message_size: int = DEFAULT_MAX_MESSAGE
    max_frame_size: int = DEFAULT_MAX_FRAME
    _buffer: bytearray = field(default_factory=bytearray, repr=False)
    _fragments: bytearray = field(default_factory=bytearray, repr=False)
    _fragment_opcode: int | None = field(default=None, repr=False)
    _fragment_size: int = field(default=0, repr=False)
    _error: ProtocolError | None = field(default=None, repr=False)
    _closed: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_message_size < 1 or self.max_frame_size < 1:
            raise ValueError("the size limits must be positive")

    # -- public ---------------------------------------------------------- #
    def feed(self, chunk: bytes) -> list[Event]:
        """Consume bytes and return every event that is now complete."""
        if self._error is not None:
            raise ProtocolError(self._error.code, self._error.reason)
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("feed() takes bytes")
        self._buffer.extend(chunk)

        events: list[Event] = []
        while True:
            frame = self._take()
            if frame is None:
                return events
            events.extend(self._handle(frame))

    @property
    def failed(self) -> bool:
        return self._error is not None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pending_bytes(self) -> int:
        """How many buffered bytes belong to a frame that is not complete yet."""
        return len(self._buffer)

    # -- parsing --------------------------------------------------------- #
    def _take(self) -> _Raw | None:
        buffer = self._buffer
        if len(buffer) < 2:
            return None

        first, second = buffer[0], buffer[1]
        fin = bool(first & 0x80)
        rsv = (first >> 4) & 0x07
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        offset = 2

        if rsv:
            raise self._fail(1002, "reserved bits must be zero")
        if opcode in RESERVED_OPCODES:
            raise self._fail(1002, f"reserved opcode {opcode}")
        if self.require_masking is True and not masked:
            raise self._fail(1002, "client frames must be masked")
        if self.require_masking is False and masked:
            raise self._fail(1002, "server frames must not be masked")

        if length == 126:
            if len(buffer) < 4:
                return None
            length = struct.unpack(">H", buffer[2:4])[0]
            offset = 4
            if length < 126:
                raise self._fail(1002, "payload length is not in its shortest form")
        elif length == 127:
            if len(buffer) < 10:
                return None
            length = struct.unpack(">Q", buffer[2:10])[0]
            offset = 10
            if length >> 63:
                raise self._fail(1002, "the payload length has its high bit set")
            if length <= 0xFFFF:
                raise self._fail(1002, "payload length is not in its shortest form")

        if length > self.max_frame_size:
            raise self._fail(1009, f"frame of {length} bytes exceeds the limit")

        if opcode in CONTROL_OPCODES:
            if not fin:
                raise self._fail(1002, "control frames must not be fragmented")
            if length > 125:
                raise self._fail(1002, "control frames must be 125 bytes or fewer")

        mask = b""
        if masked:
            if len(buffer) < offset + 4:
                return None
            mask = bytes(buffer[offset : offset + 4])
            offset += 4
        if len(buffer) < offset + length:
            return None

        payload = bytes(buffer[offset : offset + length])
        del buffer[: offset + length]
        if masked:
            payload = xor_mask(payload, mask)
        return _Raw(fin=fin, opcode=opcode, payload=payload, masked=masked)

    def _handle(self, frame: _Raw) -> list[Event]:
        if frame.opcode == PING:
            return [Ping(data=frame.payload)]
        if frame.opcode == PONG:
            return [Pong(data=frame.payload)]
        if frame.opcode == CLOSE:
            return [self._close(frame.payload)]
        if frame.opcode == CONTINUATION:
            return self._continue(frame)
        return self._start(frame)

    # -- data messages --------------------------------------------------- #
    def _start(self, frame: _Raw) -> list[Event]:
        if self._fragment_opcode is not None:
            raise self._fail(1002, "a new message started before the previous one finished")
        if len(frame.payload) > self.max_message_size:
            raise self._fail(1009, f"message of {len(frame.payload)} bytes exceeds the limit")
        if frame.fin:
            return [self._complete(frame.opcode, frame.payload)]
        self._fragment_opcode = frame.opcode
        self._fragments = bytearray(frame.payload)
        self._fragment_size = len(frame.payload)
        return []

    def _continue(self, frame: _Raw) -> list[Event]:
        if self._fragment_opcode is None:
            raise self._fail(1002, "a continuation frame arrived with no message open")
        self._fragment_size += len(frame.payload)
        if self._fragment_size > self.max_message_size:
            raise self._fail(1009, f"message of {self._fragment_size} bytes exceeds the limit")
        self._fragments.extend(frame.payload)
        if not frame.fin:
            return []
        opcode, payload = self._fragment_opcode, bytes(self._fragments)
        self._fragment_opcode = None
        self._fragments = bytearray()
        self._fragment_size = 0
        return [self._complete(opcode, payload)]

    def _complete(self, opcode: int, payload: bytes) -> Event:
        if opcode == BINARY:
            return Binary(data=payload)
        try:
            return Text(text=payload.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise self._fail(1007, "invalid UTF-8 in a text message") from error

    # -- control --------------------------------------------------------- #
    def _close(self, payload: bytes) -> Close:
        if len(payload) == 1:
            raise self._fail(1002, "a close frame with one payload byte is invalid")
        if not payload:
            self._closed = True
            return Close(code=None, reason="")

        code = struct.unpack(">H", payload[:2])[0]
        if code in FORBIDDEN_CLOSE_CODES or not (
            1000 <= code <= 1003 or 1007 <= code <= 1011 or 3000 <= code <= 4999
        ):
            raise self._fail(1002, f"invalid close code {code}")
        try:
            reason = payload[2:].decode("utf-8")
        except UnicodeDecodeError as error:
            raise self._fail(1007, "invalid UTF-8 in the close reason") from error
        self._closed = True
        return Close(code=code, reason=reason)

    # -- errors ---------------------------------------------------------- #
    def _fail(self, code: int, reason: str) -> ProtocolError:
        error = ProtocolError(code, reason)
        self._error = error
        self._buffer.clear()
        return error
