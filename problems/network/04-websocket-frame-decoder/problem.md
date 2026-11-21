---
title: WebSocket frame decoder
category: network
level: hard
concepts: RFC 6455, framing, incremental parsing, masking, fragmentation, UTF-8
status: solved
---

# WebSocket frame decoder

## Context

A WebSocket is a TCP stream with framing on top. Two facts make a decoder hard, and
both are invisible until production:

1. **Frames arrive in arbitrary chunks.** One `recv()` can deliver half a header, three
   frames, or one byte of a 70 KB binary payload. A decoder that assumes "one read =
   one frame" works on localhost and fails on a mobile network.
2. **Bad input must be answered with a close code, not a crash.** Reserved bits set →
   `1002`; invalid UTF-8 in a text message → `1007`; a message larger than your limit →
   `1009`. The peer needs to know *why*, and the reason is part of the protocol.

On top of that: client frames are masked (and server frames must not be), control
frames may be interleaved between the fragments of a data message, and a fragmented
UTF-8 character may straddle two frames — so UTF-8 can only be validated once the
message is complete.

This decoder is a state machine you feed bytes into. It emits events, keeps partial
state, and refuses to continue after a protocol error.

## Input / Output

```
feed(chunk) -> [Text | Binary | Ping | Pong | Close]   (zero or more events)
errors      -> ProtocolError(code, reason) with the RFC close code that must be sent
```

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Feed bytes incrementally; a partial frame is buffered, never misparsed | `recv()` sizes are arbitrary |
| 2 | Payload lengths use 126/127 extended forms, and the shortest form is enforced | A non-minimal length is a protocol error, not a quirk |
| 3 | Masked payloads are unmasked with the 4-byte key; masking policy is configurable | Clients mask, servers must not |
| 4 | Fragments are reassembled (`opcode 0`), with control frames allowed in between | A ping in the middle must not corrupt a message |
| 5 | RSV bits, reserved opcodes, fragmented control frames and oversized control frames are `1002` | These are the classic "just ignore it" bugs |
| 6 | Invalid UTF-8 in a text message is `1007`; a size over the limit is `1009` | The close code is the API |
| 7 | Close codes are validated (1000–1003, 1007–1011, 3000–4999); 1005/1006/1015 are refused | Those codes may never appear on the wire |
| 8 | After a protocol error the decoder stays failed and re-raises | Continuing after a desync produces garbage |

## Constraints & acceptance criteria

- Standard library only; no sockets. The decoder takes bytes and returns events, so it
  is fully testable byte by byte.
- `feed(b"")` never errors; feeding a frame one byte at a time yields exactly the same
  events as feeding it whole.
- Every rejected frame names the close code the caller must send.
- UTF-8 is validated after reassembly, so a character split across fragments is legal.
- `encode_frame` is included: it is the mirror image used by the tests, and it makes
  the masking rule testable without a peer.

## Assumptions (write them down — real problems are under-specified)

- No extension is negotiated, so any RSV bit is an error.
- The default limits are a 1 MiB message and a 1 MiB frame; both are constructor
  arguments, and the limit is a policy decision, not an RFC one.
- A close frame ends the conversation: the decoder emits `Close` and does not try to
  interpret anything that follows.
- Text payloads are UTF-8 (RFC 6455 requires it); binary payloads are opaque.
- Masking policy defaults to "accept both" so the decoder can be used for either side
  of the connection; `require_masking=True` is the client→server setting.

## How to run

```bash
cd problems/network/04-websocket-frame-decoder
python -m unittest discover -s tests -t .
python -m solution.cli --hex 8105 68656c6c6f
python -m solution.cli --file frames.bin --chunk 1 --json
```

## Stretch goals

- An encoder loop that negotiates `permessage-deflate` and sets RSV1 correctly.
- A frame *writer* with backpressure, sharing the same constants.
- Permission-bit UTF-8 validation (the RFC's own algorithm) instead of `decode`.
