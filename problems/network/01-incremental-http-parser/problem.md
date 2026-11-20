---
title: Incremental HTTP/1.1 request parser
category: network
level: hard
concepts: byte-stream parsing, state machines, chunked encoding, request smuggling, limits
status: solved
---

# Incremental HTTP/1.1 request parser

## Context

You are writing the HTTP layer of a server (or a load balancer) and reading from a
TCP socket. The kernel hands you bytes in whatever sizes it feels like: half a request
line, a header split mid-value, half a body. You cannot `await read_until(b"\r\n\r\n")`
and hope — the buffer arrives in pieces, and clients send hostile ones.

This is the parser that turns a byte stream into `Request` objects, with limits, a
strict grammar, and explicit errors that map to HTTP status codes.

## Input / Output

```python
parser = HttpRequestParser()
parser.feed(b"GET /health HTTP/1.1\r\nHost: api\r\n")   # -> None (incomplete)
request = parser.feed(b"Accept: */*\r\n\r\n")            # -> Request(...)
request.method, request.target, request.header("host")
```

## Requirements

| # | Requirement | Why |
|---|-------------|-----|
| 1 | Any chunking of the same bytes yields the same `Request` | TCP does not respect your message boundaries |
| 2 | Never blocks on incomplete input: `feed` returns `None`, buffer stays intact | the event loop must keep running |
| 3 | `Content-Length` bodies read exactly that many bytes, no more | reading more steals the next request |
| 4 | `Transfer-Encoding: chunked` decoded, including chunk extensions and trailers | real clients send both |
| 5 | Pipelined requests: leftover bytes stay buffered for the next `feed(b"")` | HTTP/1.1 keep-alive |
| 6 | Header limits: 8 KiB of headers → `431`, body over 1 MiB → `413` | resource protection |
| 7 | Reject request smuggling vectors: duplicate `Content-Length`, both `CL` and `TE`, obs-fold headers, LF-only line endings | these are security bugs, not style |
| 8 | After an error the parser refuses further input | a desynchronised stream cannot be trusted |

## Constraints & acceptance criteria

- `ParseError` carries a `.status` (400, 413, 431, 501, 505) so the server can answer
  without guessing.
- Header lookup is case-insensitive; header names are stored lower-cased.
- `Request.keep_alive` follows RFC semantics: HTTP/1.1 defaults to keep-alive unless
  `Connection: close`; HTTP/1.0 needs an explicit `Connection: keep-alive`.
- Unknown transfer coding → `501`, unsupported version → `505`.
- Bodies are `bytes`; header blocks are decoded as latin-1 (byte-transparent) so binary
  garbage in a header does not raise `UnicodeDecodeError`.

## Documented limits

- Trailer headers after a chunked body are read and discarded, not exposed.
- No `Expect: 100-continue` handling: the parser is transport-agnostic and the caller
  decides when to write the interim response.
- HTTP/2 and h2c are out of scope, by definition (different framing entirely).

## How to run

```bash
cd problems/network/01-incremental-http-parser
python -m unittest discover -s tests -t .
python -m solution.cli tests/fixtures/post.http --chunk-size 7
```

## Stretch goals

- A `Response` parser sharing the header machinery.
- Backpressure: return `None` and stop buffering past `max_header_bytes`.
- Fuzz the parser with `atheris` and assert "no crash, only `ParseError`".
