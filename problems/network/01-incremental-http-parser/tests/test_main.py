"""Tests for the incremental HTTP parser: framing, chunking, limits, smuggling."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from solution import cli
from solution.main import HttpRequestParser, ParseError, Request

FIXTURES = Path(__file__).parent / "fixtures"

SIMPLE = b"GET /health HTTP/1.1\r\nHost: api.example\r\nAccept: */*\r\n\r\n"
POST = (
    b"POST /v1/items HTTP/1.1\r\n"
    b"Host: api.example\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 13\r\n\r\n"
    b'{"ok": true}\n'
)
CHUNKED = (
    b"POST /v1/stream HTTP/1.1\r\n"
    b"Host: api.example\r\n"
    b"Transfer-Encoding: chunked\r\n\r\n"
    b"5;ext=1\r\nhello\r\n"
    b"6\r\n world\r\n"
    b"0\r\nX-Trailer: done\r\n\r\n"
)


class TestFraming(unittest.TestCase):
    def test_simple_request(self) -> None:
        parser = HttpRequestParser()
        self.assertIsNone(parser.feed(b"GET /health HTTP/1.1\r\nHost: api\r\n"))
        request = parser.feed(b"Accept: */*\r\n\r\n")
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual((request.method, request.target, request.version), ("GET", "/health", "HTTP/1.1"))
        self.assertEqual(request.header("host"), "api")
        self.assertEqual(request.header("HOST"), "api")
        self.assertIsNone(request.header("missing"))
        self.assertEqual(request.body, b"")

    def test_header_case_is_normalised(self) -> None:
        parser = HttpRequestParser()
        request = parser.feed(b"GET / HTTP/1.1\r\nX-Request-Id: abc\r\n\r\n")
        assert request is not None
        self.assertEqual(request.headers["x-request-id"], "abc")

    def test_any_chunking_gives_the_same_request(self) -> None:
        expected = HttpRequestParser().feed(POST)
        for size in range(1, len(POST) + 1):
            with self.subTest(size=size):
                parser = HttpRequestParser()
                request = None
                for offset in range(0, len(POST), size):
                    parsed = parser.feed(POST[offset : offset + size])
                    if parsed is not None:
                        request = parsed
                self.assertEqual(request, expected)

    def test_content_length_body(self) -> None:
        request = HttpRequestParser().feed(POST)
        assert request is not None
        self.assertEqual(request.body, b'{"ok": true}\n')
        self.assertEqual(request.header("content-length"), "13")

    def test_zero_content_length(self) -> None:
        request = HttpRequestParser().feed(b"GET /x HTTP/1.1\r\nContent-Length: 0\r\n\r\n")
        assert request is not None
        self.assertEqual(request.body, b"")

    def test_incomplete_body_waits(self) -> None:
        parser = HttpRequestParser()
        self.assertIsNone(parser.feed(POST[:40]))
        self.assertIsNone(parser.feed(POST[40:-1]))
        self.assertIsNotNone(parser.feed(POST[-1:]))

    def test_empty_feed_returns_none(self) -> None:
        parser = HttpRequestParser()
        self.assertIsNone(parser.feed(b""))
        self.assertEqual(parser.buffered_bytes, 0)


class TestChunked(unittest.TestCase):
    def test_chunked_body_with_extensions_and_trailers(self) -> None:
        request = HttpRequestParser().feed(CHUNKED)
        assert request is not None
        self.assertTrue(request.is_chunked)
        self.assertEqual(request.body, b"hello world")
        self.assertEqual(request.header("transfer-encoding"), "chunked")

    def test_chunked_byte_by_byte(self) -> None:
        parser = HttpRequestParser()
        request = None
        for index in range(len(CHUNKED)):
            parsed = parser.feed(CHUNKED[index : index + 1])
            if parsed is not None:
                request = parsed
        assert request is not None
        self.assertEqual(request.body, b"hello world")

    def test_chunk_size_is_hexadecimal(self) -> None:
        raw = (
            b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"1a\r\n" + b"x" * 26 + b"\r\n0\r\n\r\n"
        )
        request = HttpRequestParser().feed(raw)
        assert request is not None
        self.assertEqual(len(request.body), 26)

    def test_chunk_missing_crlf_is_rejected(self) -> None:
        raw = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabcX\r\n0\r\n\r\n"
        with self.assertRaises(ParseError) as caught:
            HttpRequestParser().feed(raw)
        self.assertEqual(caught.exception.status, 400)

    def test_empty_chunked_body(self) -> None:
        raw = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
        request = HttpRequestParser().feed(raw)
        assert request is not None
        self.assertEqual(request.body, b"")


class TestPipelining(unittest.TestCase):
    def test_second_request_stays_buffered(self) -> None:
        parser = HttpRequestParser()
        first = parser.feed(SIMPLE + SIMPLE)
        self.assertIsNotNone(first)
        self.assertGreater(parser.buffered_bytes, 0)

        second = parser.feed(b"")
        assert second is not None
        self.assertEqual(second.target, "/health")
        self.assertEqual(parser.buffered_bytes, 0)
        self.assertIsNone(parser.feed(b""))


class TestLimitsAndErrors(unittest.TestCase):
    def test_header_section_too_large(self) -> None:
        parser = HttpRequestParser(max_header_bytes=64)
        with self.assertRaises(ParseError) as caught:
            parser.feed(b"GET / HTTP/1.1\r\n" + b"X-Pad: " + b"a" * 100)
        self.assertEqual(caught.exception.status, 431)

    def test_too_many_headers(self) -> None:
        parser = HttpRequestParser(max_headers=3)
        with self.assertRaises(ParseError) as caught:
            parser.feed(b"GET / HTTP/1.1\r\na: 1\r\nb: 2\r\nc: 3\r\nd: 4\r\n\r\n")
        self.assertEqual(caught.exception.status, 431)

    def test_body_too_large(self) -> None:
        parser = HttpRequestParser(max_body_bytes=10)
        with self.assertRaises(ParseError) as caught:
            parser.feed(b"POST / HTTP/1.1\r\nContent-Length: 11\r\n\r\n")
        self.assertEqual(caught.exception.status, 413)

    def test_chunked_body_too_large(self) -> None:
        parser = HttpRequestParser(max_body_bytes=4)
        raw = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
        with self.assertRaises(ParseError) as caught:
            parser.feed(raw)
        self.assertEqual(caught.exception.status, 413)

    def test_duplicate_content_length_is_rejected(self) -> None:
        raw = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 5\r\n\r\nhello"
        with self.assertRaises(ParseError):
            HttpRequestParser().feed(raw)

    def test_content_length_with_transfer_encoding_is_rejected(self) -> None:
        raw = (
            b"POST / HTTP/1.1\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"0\r\n\r\n"
        )
        with self.assertRaises(ParseError):
            HttpRequestParser().feed(raw)

    def test_unknown_transfer_coding(self) -> None:
        raw = b"POST / HTTP/1.1\r\nTransfer-Encoding: gzip\r\n\r\n"
        with self.assertRaises(ParseError) as caught:
            HttpRequestParser().feed(raw)
        self.assertEqual(caught.exception.status, 501)

    def test_obs_fold_is_rejected(self) -> None:
        raw = b"GET / HTTP/1.1\r\nX-Long: a\r\n  b\r\n\r\n"
        with self.assertRaises(ParseError) as caught:
            HttpRequestParser().feed(raw)
        self.assertEqual(caught.exception.status, 400)

    def test_bare_lf_line_endings_are_rejected(self) -> None:
        with self.assertRaises(ParseError) as caught:
            HttpRequestParser().feed(b"GET / HTTP/1.1\nHost: api\n\n")
        self.assertEqual(caught.exception.status, 400)

    def test_bad_request_line(self) -> None:
        for raw in (
            b"GET /\r\n\r\n",
            b"get / HTTP/1.1\r\n\r\n",
            b"GET / HTTP/9.9\r\n\r\n",
            b"GET / HTTP/1.1 extra\r\n\r\n",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ParseError):
                    HttpRequestParser().feed(raw)

    def test_header_without_colon(self) -> None:
        with self.assertRaises(ParseError):
            HttpRequestParser().feed(b"GET / HTTP/1.1\r\nBadHeader\r\n\r\n")

    def test_invalid_content_length(self) -> None:
        for value in (b"-1", b"abc", b"1.5", b""):
            with self.subTest(value=value):
                with self.assertRaises(ParseError):
                    HttpRequestParser().feed(
                        b"POST / HTTP/1.1\r\nContent-Length: " + value + b"\r\n\r\n"
                    )

    def test_unsupported_version(self) -> None:
        with self.assertRaises(ParseError) as caught:
            HttpRequestParser().feed(b"GET / HTTP/2.0\r\n\r\n")
        self.assertEqual(caught.exception.status, 505)

    def test_parser_refuses_input_after_error(self) -> None:
        parser = HttpRequestParser()
        with self.assertRaises(ParseError):
            parser.feed(b"garbage\r\n\r\n")
        with self.assertRaises(ParseError):
            parser.feed(SIMPLE)

    def test_feed_requires_bytes(self) -> None:
        with self.assertRaises(TypeError):
            HttpRequestParser().feed("GET / HTTP/1.1")  # type: ignore[arg-type]

    def test_invalid_limits(self) -> None:
        for kwargs in ({"max_header_bytes": 0}, {"max_body_bytes": -1}, {"max_headers": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    HttpRequestParser(**kwargs)


class TestKeepAlive(unittest.TestCase):
    def request(self, version: bytes, connection: bytes | None = None) -> Request:
        headers = b"Host: api\r\n"
        if connection is not None:
            headers += b"Connection: " + connection + b"\r\n"
        parsed = HttpRequestParser().feed(b"GET / " + version + b"\r\n" + headers + b"\r\n")
        assert parsed is not None
        return parsed

    def test_http_11_defaults_to_keep_alive(self) -> None:
        self.assertTrue(self.request(b"HTTP/1.1").keep_alive)

    def test_http_11_connection_close(self) -> None:
        self.assertFalse(self.request(b"HTTP/1.1", b"close").keep_alive)

    def test_http_10_needs_explicit_keep_alive(self) -> None:
        self.assertFalse(self.request(b"HTTP/1.0").keep_alive)
        self.assertTrue(self.request(b"HTTP/1.0", b"keep-alive").keep_alive)


class TestFixturesAndCli(unittest.TestCase):
    def test_post_fixture_parses(self) -> None:
        request = HttpRequestParser().feed((FIXTURES / "post.http").read_bytes())
        assert request is not None
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.target, "/v1/items")
        self.assertEqual(request.header("content-type"), "application/json")
        self.assertEqual(len(request.body), 13)

    def test_chunked_fixture_parses(self) -> None:
        request = HttpRequestParser().feed((FIXTURES / "chunked.http").read_bytes())
        assert request is not None
        self.assertEqual(request.body, b"hello world")

    def test_cli_reports_progress(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            exit_code = cli.main([str(FIXTURES / "post.http"), "--chunk-size", "7"])
        output = out.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("POST /v1/items HTTP/1.1", output)
        self.assertIn("parsed 1 request(s)", output)

    def test_cli_reports_parse_errors(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(FIXTURES / "missing.http")]), 1)

    def test_cli_rejects_zero_chunk_size(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(FIXTURES / "post.http"), "--chunk-size", "0"]), 2)


if __name__ == "__main__":
    unittest.main()
