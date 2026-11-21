"""Tests for the WebSocket decoder: framing, masking, fragmentation, close codes."""
from __future__ import annotations

import io
import json
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import (
    Binary,
    Close,
    Decoder,
    Ping,
    Pong,
    ProtocolError,
    Text,
    encode_frame,
    xor_mask,
)

MASK = b"\x37\xfa\x21\x3d"


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def decode(data: bytes, **kwargs: object) -> list:
    decoder = Decoder(**kwargs)  # type: ignore[arg-type]
    events = []
    for byte in data:
        events.extend(decoder.feed(bytes([byte])))
    return events


def code_of(data: bytes, **kwargs: object) -> tuple[int, str]:
    try:
        decode(data, **kwargs)
    except ProtocolError as error:
        return error.code, error.reason
    raise AssertionError("expected a ProtocolError")


class TestBasicFrames(unittest.TestCase):
    def test_text(self) -> None:
        self.assertEqual(Decoder().feed(encode_frame(0x1, b"hello")), [Text(text="hello")])

    def test_binary(self) -> None:
        self.assertEqual(
            Decoder().feed(encode_frame(0x2, b"\x00\x01\x02")), [Binary(data=b"\x00\x01\x02")]
        )

    def test_empty_text(self) -> None:
        self.assertEqual(Decoder().feed(encode_frame(0x1, b"")), [Text(text="")])

    def test_ping_and_pong(self) -> None:
        self.assertEqual(Decoder().feed(encode_frame(0x9, b"hb")), [Ping(data=b"hb")])
        self.assertEqual(Decoder().feed(encode_frame(0xA, b"ok")), [Pong(data=b"ok")])

    def test_several_frames_in_one_chunk(self) -> None:
        data = encode_frame(0x1, b"a") + encode_frame(0x9, b"") + encode_frame(0x2, b"\xff")
        self.assertEqual(
            Decoder().feed(data), [Text(text="a"), Ping(data=b""), Binary(data=b"\xff")]
        )

    def test_empty_feed_is_harmless(self) -> None:
        self.assertEqual(Decoder().feed(b""), [])

    def test_a_partial_frame_is_buffered(self) -> None:
        decoder = Decoder()
        frame = encode_frame(0x1, b"hello")
        self.assertEqual(decoder.feed(frame[:3]), [])
        self.assertEqual(decoder.pending_bytes, 3)
        self.assertEqual(decoder.feed(frame[3:]), [Text(text="hello")])
        self.assertEqual(decoder.pending_bytes, 0)

    def test_a_str_is_not_bytes(self) -> None:
        with self.assertRaises(TypeError):
            Decoder().feed("hello")  # type: ignore[arg-type]


class TestLengths(unittest.TestCase):
    def test_sixteen_bit_length(self) -> None:
        payload = b"x" * 300
        self.assertEqual(Decoder().feed(encode_frame(0x2, payload)), [Binary(data=payload)])

    def test_sixty_four_bit_length(self) -> None:
        payload = b"y" * 70000
        events = decode(encode_frame(0x2, payload))
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].data), 70000)

    def test_a_non_minimal_sixteen_bit_length_is_refused(self) -> None:
        frame = bytes([0x81, 126]) + struct.pack(">H", 5) + b"hello"
        self.assertEqual(code_of(frame)[0], 1002)

    def test_a_non_minimal_sixty_four_bit_length_is_refused(self) -> None:
        frame = bytes([0x81, 127]) + struct.pack(">Q", 300) + b"x" * 300
        self.assertEqual(code_of(frame)[0], 1002)

    def test_a_length_with_the_high_bit_set_is_refused(self) -> None:
        frame = bytes([0x81, 127]) + struct.pack(">Q", 1 << 63) + b""
        self.assertEqual(code_of(frame)[0], 1002)

    def test_frame_size_limit(self) -> None:
        code, reason = code_of(encode_frame(0x2, b"z" * 50), max_frame_size=10)
        self.assertEqual(code, 1009)
        self.assertIn("limit", reason)


class TestMasking(unittest.TestCase):
    def test_masking_is_reversible(self) -> None:
        payload = b"the quick brown fox"
        self.assertEqual(xor_mask(xor_mask(payload, MASK), MASK), payload)

    def test_a_masked_text_frame_decodes(self) -> None:
        frame = encode_frame(0x1, b"hi", mask=MASK)
        self.assertEqual(Decoder(require_masking=True).feed(frame), [Text(text="hi")])

    def test_masking_is_applied_before_the_extended_length(self) -> None:
        payload = b"a" * 200
        frame = encode_frame(0x2, payload, mask=MASK)
        self.assertEqual(frame[1] & 0x80, 0x80)
        self.assertEqual(Decoder(require_masking=True).feed(frame), [Binary(data=payload)])

    def test_client_frames_must_be_masked(self) -> None:
        code, reason = code_of(encode_frame(0x1, b"x"), require_masking=True)
        self.assertEqual((code, reason), (1002, "client frames must be masked"))

    def test_server_frames_must_not_be_masked(self) -> None:
        code, _ = code_of(encode_frame(0x1, b"x", mask=MASK), require_masking=False)
        self.assertEqual(code, 1002)

    def test_both_directions_are_accepted_by_default(self) -> None:
        decoder = Decoder()
        self.assertEqual(decoder.feed(encode_frame(0x1, b"a")), [Text(text="a")])
        self.assertEqual(decoder.feed(encode_frame(0x1, b"b", mask=MASK)), [Text(text="b")])

    def test_a_bad_mask_key_is_refused_by_the_encoder(self) -> None:
        with self.assertRaises(ValueError):
            encode_frame(0x1, b"x", mask=b"abc")
        with self.assertRaises(ValueError):
            xor_mask(b"x", b"abc")


class TestFragmentation(unittest.TestCase):
    def test_two_fragments(self) -> None:
        data = encode_frame(0x1, b"hel", fin=False) + encode_frame(0x0, b"lo", fin=True)
        self.assertEqual(Decoder().feed(data), [Text(text="hello")])

    def test_three_fragments(self) -> None:
        data = b"".join(
            [
                encode_frame(0x2, b"\x01", fin=False),
                encode_frame(0x0, b"\x02", fin=False),
                encode_frame(0x0, b"\x03", fin=True),
            ]
        )
        self.assertEqual(Decoder().feed(data), [Binary(data=b"\x01\x02\x03")])

    def test_a_control_frame_may_interleave(self) -> None:
        decoder = Decoder()
        events = decoder.feed(encode_frame(0x1, b"he", fin=False))
        events += decoder.feed(encode_frame(0x9, b"ping"))
        events += decoder.feed(encode_frame(0x0, b"llo", fin=True))
        self.assertEqual(events, [Ping(data=b"ping"), Text(text="hello")])

    def test_a_multibyte_character_may_straddle_fragments(self) -> None:
        encoded = "héllo".encode("utf-8")
        data = encode_frame(0x1, encoded[:2], fin=False) + encode_frame(0x0, encoded[2:], fin=True)
        self.assertEqual(Decoder().feed(data), [Text(text="héllo")])

    def test_a_continuation_without_a_start_is_refused(self) -> None:
        code, reason = code_of(encode_frame(0x0, b"x"))
        self.assertEqual(code, 1002)
        self.assertIn("no message open", reason)

    def test_a_new_message_during_fragmentation_is_refused(self) -> None:
        data = encode_frame(0x1, b"a", fin=False) + encode_frame(0x1, b"b")
        self.assertEqual(code_of(data)[0], 1002)

    def test_message_size_limit_on_a_single_frame(self) -> None:
        code, _ = code_of(encode_frame(0x1, b"a" * 40), max_message_size=20)
        self.assertEqual(code, 1009)

    def test_message_size_limit_across_fragments(self) -> None:
        data = encode_frame(0x1, b"a" * 15, fin=False) + encode_frame(0x0, b"b" * 15, fin=True)
        self.assertEqual(code_of(data, max_message_size=20)[0], 1009)


class TestProtocolErrors(unittest.TestCase):
    def test_reserved_bits(self) -> None:
        frame = bytes([0xC1, 0x01]) + b"x"  # RSV1 set
        code, reason = code_of(frame)
        self.assertEqual(code, 1002)
        self.assertIn("reserved bits", reason)

    def test_reserved_opcodes(self) -> None:
        for opcode in (0x3, 0x7, 0xB, 0xF):
            with self.subTest(opcode=opcode):
                self.assertEqual(code_of(encode_frame(opcode, b"x"))[0], 1002)

    def test_a_fragmented_control_frame(self) -> None:
        code, reason = code_of(encode_frame(0x9, b"x", fin=False))
        self.assertEqual(code, 1002)
        self.assertIn("not be fragmented", reason)

    def test_an_oversized_control_frame(self) -> None:
        code, reason = code_of(encode_frame(0x9, b"x" * 126))
        self.assertEqual(code, 1002)
        self.assertIn("125", reason)

    def test_invalid_utf8_in_a_text_message(self) -> None:
        code, reason = code_of(encode_frame(0x1, b"\xff\xfe"))
        self.assertEqual(code, 1007)
        self.assertIn("UTF-8", reason)

    def test_invalid_utf8_split_across_fragments_is_refused_at_the_end(self) -> None:
        data = encode_frame(0x1, b"ok\xff", fin=False) + encode_frame(0x0, b"x", fin=True)
        self.assertEqual(code_of(data)[0], 1007)

    def test_the_decoder_stays_failed(self) -> None:
        decoder = Decoder()
        with self.assertRaises(ProtocolError) as first:
            decoder.feed(bytes([0xC1, 0x01]) + b"x")
        self.assertTrue(decoder.failed)
        with self.assertRaises(ProtocolError) as second:
            decoder.feed(encode_frame(0x1, b"fine"))
        self.assertEqual(first.exception.code, second.exception.code)


class TestClose(unittest.TestCase):
    def test_code_and_reason(self) -> None:
        frame = encode_frame(0x8, struct.pack(">H", 1000) + b"bye")
        self.assertEqual(Decoder().feed(frame), [Close(code=1000, reason="bye")])

    def test_code_only(self) -> None:
        frame = encode_frame(0x8, struct.pack(">H", 1001))
        self.assertEqual(Decoder().feed(frame), [Close(code=1001, reason="")])

    def test_no_payload(self) -> None:
        self.assertEqual(Decoder().feed(encode_frame(0x8, b"")), [Close(code=None, reason="")])

    def test_the_closed_flag_is_set(self) -> None:
        decoder = Decoder()
        decoder.feed(encode_frame(0x8, struct.pack(">H", 1000)))
        self.assertTrue(decoder.closed)

    def test_valid_codes(self) -> None:
        for code_number in (1000, 1001, 1002, 1003, 1007, 1008, 1009, 1011, 3000, 4999):
            with self.subTest(code=code_number):
                events = Decoder().feed(encode_frame(0x8, struct.pack(">H", code_number)))
                self.assertEqual(events[0].code, code_number)

    def test_codes_that_may_not_be_sent(self) -> None:
        for code_number in (1004, 1005, 1006, 1015, 999, 1016, 2999, 5000):
            with self.subTest(code=code_number):
                frame = encode_frame(0x8, struct.pack(">H", code_number))
                self.assertEqual(code_of(frame)[0], 1002)

    def test_a_one_byte_close_payload(self) -> None:
        self.assertEqual(code_of(encode_frame(0x8, b"\x03"))[0], 1002)

    def test_an_invalid_utf8_reason(self) -> None:
        frame = encode_frame(0x8, struct.pack(">H", 1000) + b"\xff")
        self.assertEqual(code_of(frame)[0], 1007)


class TestStreamingEquivalence(unittest.TestCase):
    def test_byte_at_a_time_matches_a_single_feed(self) -> None:
        stream = b"".join(
            [
                encode_frame(0x1, b"one", mask=MASK),
                encode_frame(0x1, b"tw", fin=False),
                encode_frame(0x9, b"p"),
                encode_frame(0x0, b"o"),
                encode_frame(0x2, b"\x00" * 200),
                encode_frame(0x8, struct.pack(">H", 1000)),
            ]
        )
        whole = Decoder().feed(stream)
        bytewise = decode(stream)
        self.assertEqual([event.kind for event in whole], ["text", "ping", "text", "binary", "close"])
        self.assertEqual(
            [type(event) for event in bytewise], [type(event) for event in whole]
        )
        self.assertEqual(bytewise, whole)

    def test_random_chunk_boundaries(self) -> None:
        import random

        generator = random.Random(5)
        payloads = [b"a", b"", b"bb", b"ccc"]
        frames = b"".join(encode_frame(0x2, payload) for payload in payloads)
        whole = Decoder().feed(frames)

        decoder = Decoder()
        events = []
        index = 0
        while index < len(frames):
            size = generator.randint(1, 5)
            events.extend(decoder.feed(frames[index : index + size]))
            index += size
        self.assertEqual(events, whole)


class TestEncode(unittest.TestCase):
    def test_round_trip(self) -> None:
        for length in (0, 1, 125, 126, 127, 65535, 65536):
            payload = b"q" * length
            with self.subTest(length=length):
                events = Decoder(max_frame_size=100_000, max_message_size=100_000).feed(
                    encode_frame(0x2, payload)
                )
                self.assertEqual(events, [Binary(data=payload)])

    def test_invalid_opcode(self) -> None:
        with self.assertRaises(ValueError):
            encode_frame(0x10, b"")


class TestCli(unittest.TestCase):
    def test_hex_text(self) -> None:
        code, out, err = run_cli("--hex", "8105 68656c6c6f")
        self.assertEqual(code, 0)
        self.assertIn("text 'hello'", out)
        self.assertIn("1 event(s)", err)

    def test_json_output(self) -> None:
        code, out, _ = run_cli("--hex", "880203e8", "--json")
        self.assertEqual(code, 0)
        events = json.loads(out)
        self.assertEqual(events, [{"kind": "close", "code": 1000, "reason": ""}])

    def test_chunked_feeding(self) -> None:
        code, out, _ = run_cli("--hex", "8103 616263", "--chunk", "1")
        self.assertEqual(code, 0)
        self.assertIn("text 'abc'", out)

    def test_a_protocol_error_exits_one(self) -> None:
        code, _, err = run_cli("--hex", "c10178")  # RSV1 set
        self.assertEqual(code, 1)
        self.assertIn("1002", err)

    def test_masking_policy(self) -> None:
        code, _, err = run_cli("--hex", "810178", "--masking", "required")
        self.assertEqual(code, 1)
        self.assertIn("masked", err)

    def test_bad_hex_exits_two(self) -> None:
        code, _, err = run_cli("--hex", "zz")
        self.assertEqual(code, 2)
        self.assertIn("not valid hex", err)

    def test_reading_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            path = os.path.join(sandbox, "frames.bin")
            with open(path, "wb") as handle:
                handle.write(encode_frame(0x1, b"from a file"))
            code, out, _ = run_cli("--file", path)
        self.assertEqual(code, 0)
        self.assertIn("text 'from a file'", out)

    def test_a_missing_file_exits_two(self) -> None:
        code, _, err = run_cli("--file", os.path.join(tempfile.gettempdir(), "nope.bin"))
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
