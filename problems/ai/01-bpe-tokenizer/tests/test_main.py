"""Tests for the BPE tokenizer: round-trip, training, determinism, persistence."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from solution.main import END_OF_WORD, BPETokenizer, Merge, pretokenize

FIXTURE = Path(__file__).parent / "fixtures" / "corpus.txt"


# The corpus is ASCII; these lines widen the alphabet so the round-trip tests can
# also prove that Unicode and emoji survive (a vocabulary can only encode what it
# was trained on — that is exactly why the unknown-character tests exist).
EXTRA_LINES = [
    "Hello, world!",
    "a  b",
    "trailing spaces   ",
    "   leading spaces",
    "multi\nline\ntext",
    "tabs\tand\ttabs",
    "héllo wörld — naïve café",
    "🚀 launch sequence",
]


def corpus() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def training_corpus() -> list[str]:
    return corpus() + EXTRA_LINES


class TestPretokenize(unittest.TestCase):
    def test_pieces_rebuild_the_text_exactly(self) -> None:
        for text in (
            "Hello, world!",
            "a  b",
            "trailing spaces   ",
            "   leading",
            "line\nbreak\ttab",
            "héllo wörld — naïve café",
            "🚀 launch",
            "",
        ):
            with self.subTest(text=text):
                self.assertEqual("".join(pretokenize(text)), text)

    def test_whitespace_is_attached_to_the_following_piece(self) -> None:
        self.assertEqual(pretokenize("Hello, world!"), ["Hello", ",", " world", "!"])


class TestTraining(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tok = BPETokenizer.train(corpus(), vocab_size=400)

    def test_vocab_size_is_a_hard_cap(self) -> None:
        self.assertLessEqual(self.tok.vocab_size, 400)
        self.assertGreater(self.tok.vocab_size, 100)  # training actually happened

    def test_merges_are_consistent_and_in_rank_order(self) -> None:
        self.assertGreater(len(self.tok.merges), 0)
        for index, merge in enumerate(self.tok.merges):
            self.assertEqual(merge.rank, index)
            self.assertEqual(merge.merged, merge.left + merge.right)
            self.assertIn(merge.merged, self.tok.vocab)

    def test_training_is_deterministic(self) -> None:
        again = BPETokenizer.train(corpus(), vocab_size=400)
        self.assertEqual(again.merges, self.tok.merges)
        self.assertEqual(again.vocab, self.tok.vocab)

    def test_frequency_ties_break_lexicographically(self) -> None:
        # Every pair here has frequency 2, so the lexicographically smallest wins.
        tok = BPETokenizer.train(["ab ab", "cd cd"], vocab_size=12, min_frequency=2)
        self.assertEqual(tok.merges[0].pair, ("a", "b"))

    def test_compression_beats_characters(self) -> None:
        text = "The tokenizer must be lossless, and the tokenizer must be reproducible."
        self.assertLess(len(self.tok.encode(text)), len(text))
        self.assertGreater(self.tok.compression_ratio(text), 1.5)

    def test_min_frequency_stops_training(self) -> None:
        tok = BPETokenizer.train(["ab"], vocab_size=100, min_frequency=2)
        self.assertEqual(tok.merges, ())

    def test_invalid_training_arguments(self) -> None:
        with self.assertRaises(ValueError):
            BPETokenizer.train([], vocab_size=10)  # nothing to train on
        with self.assertRaises(ValueError):
            BPETokenizer.train(corpus(), vocab_size=5)  # smaller than the alphabet
        with self.assertRaises(ValueError):
            BPETokenizer.train(corpus(), vocab_size=100, min_frequency=0)

    def test_special_tokens_are_reserved_and_never_merged(self) -> None:
        tok = BPETokenizer.train(corpus(), vocab_size=420, special_tokens=("<pad>", "<eos>"))
        self.assertIn("<pad>", tok.vocab)
        self.assertIn("<eos>", tok.vocab)
        ids = tok.encode("tokenizer")
        self.assertNotIn(tok.vocab["<pad>"], ids)


class TestRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tok = BPETokenizer.train(training_corpus(), vocab_size=420)

    def test_lossless_on_awkward_text(self) -> None:
        cases = [
            "",
            "a",
            "Hello, world!",
            "a  b",
            "trailing spaces   ",
            "   leading spaces",
            "multi\nline\ntext",
            "tabs\tand\ttabs",
            "héllo wörld — naïve café",
            "🚀 launch sequence",
            "The tokenizer must be lossless, and the tokenizer must be reproducible.",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(self.tok.decode(self.tok.encode(text)), text)

    def test_every_corpus_line_round_trips(self) -> None:
        for line in corpus():
            with self.subTest(line=line[:40]):
                self.assertEqual(self.tok.decode(self.tok.encode(line)), line)

    def test_empty_text(self) -> None:
        self.assertEqual(self.tok.encode(""), [])
        self.assertEqual(self.tok.decode([]), "")

    def test_tokens_rebuild_the_text(self) -> None:
        text = "tokenizer tokenizer"
        tokens = self.tok.tokenize(text)
        self.assertEqual("".join(tokens).replace(END_OF_WORD, ""), text)
        self.assertEqual(len(tokens), len(self.tok.encode(text)))


class TestUnknownCharacters(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tok = BPETokenizer.train(["abc abc"], vocab_size=12, min_frequency=2)

    def test_raises_without_unknown_token(self) -> None:
        with self.assertRaises(ValueError):
            self.tok.encode("héllo")
        with self.assertRaises(ValueError):
            self.tok.decode([9999])

    def test_degrades_to_unknown_token_when_configured(self) -> None:
        tok = BPETokenizer.train(
            ["abc abc"], vocab_size=12, min_frequency=2, unknown_token="<unk>"
        )
        ids = tok.encode("h")
        self.assertIn(tok.vocab["<unk>"], ids)
        self.assertIn("<unk>", tok.decode(ids))  # loss is visible, not silent

    def test_unknown_token_must_be_in_vocab(self) -> None:
        with self.assertRaises(ValueError):
            BPETokenizer({"a": 0}, [], unknown_token="<unk>")

    def test_decode_rejects_invented_ids(self) -> None:
        with self.assertRaises(ValueError):
            self.tok.decode([9999])


class TestPersistence(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        tok = BPETokenizer.train(training_corpus(), vocab_size=320)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokenizer.json"
            tok.save(path)
            loaded = BPETokenizer.load(path)

        text = " ".join(training_corpus()[:3])  # guaranteed to be in the alphabet
        self.assertEqual(loaded.encode(text), tok.encode(text))
        self.assertEqual(loaded.decode(tok.encode(text)), text)
        self.assertEqual(loaded.merges, tok.merges)
        self.assertEqual(loaded.vocab, tok.vocab)
        self.assertEqual(loaded.unknown_token, tok.unknown_token)

    def test_unknown_token_survives_a_round_trip(self) -> None:
        tok = BPETokenizer.train(
            ["abc abc"], vocab_size=12, min_frequency=2, unknown_token="<unk>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tok.json"
            tok.save(path)
            self.assertEqual(BPETokenizer.load(path).unknown_token, "<unk>")


class TestMergeDataclass(unittest.TestCase):
    def test_pair_property(self) -> None:
        merge = Merge(left="t", right="h", merged="th", rank=0)
        self.assertEqual(merge.pair, ("t", "h"))


if __name__ == "__main__":
    unittest.main()
