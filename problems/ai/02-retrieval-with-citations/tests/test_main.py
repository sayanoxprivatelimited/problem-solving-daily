"""Tests for the retrieval layer: chunking, citations, ranking, CLI."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from solution import cli
from solution.main import Chunk, Retriever, chunk_document, render, tokenize

DOCS_DIR = Path(__file__).parent / "fixtures" / "docs"


def load_docs() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(DOCS_DIR.glob("*.md"))}


class TestChunkDocument(unittest.TestCase):
    text = (
        "First sentence about keys. Second sentence about billing. "
        "Third sentence about regions, quotas and uploads. Fourth one."
    )

    def test_chunks_respect_the_size_limit(self) -> None:
        chunks = chunk_document("doc.md", self.text, size=60, overlap=10)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertLessEqual(chunk.length, 60)

    def test_citations_slice_the_original_text(self) -> None:
        chunks = chunk_document("doc.md", self.text, size=60, overlap=10)
        for chunk in chunks:
            self.assertEqual(self.text[chunk.start : chunk.end], chunk.text)
            self.assertEqual(chunk.citation, f"doc.md#chars={chunk.start}-{chunk.end}")

    def test_consecutive_chunks_overlap(self) -> None:
        chunks = chunk_document("doc.md", self.text, size=60, overlap=15)
        self.assertGreater(len(chunks), 2)
        for previous, current in zip(chunks, chunks[1:]):
            self.assertLess(current.start, previous.end)

    def test_every_character_is_covered(self) -> None:
        chunks = chunk_document("doc.md", self.text, size=50, overlap=10)
        covered = set()
        for chunk in chunks:
            covered.update(range(chunk.start, chunk.end))
        for index, character in enumerate(self.text):
            if not character.isspace():
                self.assertIn(index, covered)

    def test_prefers_sentence_boundaries(self) -> None:
        chunks = chunk_document("doc.md", self.text, size=60, overlap=0)
        self.assertTrue(chunks[0].text.rstrip().endswith("."))

    def test_falls_back_to_whitespace(self) -> None:
        text = "alpha " * 40  # no sentence endings at all
        first = chunk_document("doc.md", text, size=40, overlap=0)[0]
        self.assertLessEqual(first.length, 40)
        self.assertTrue(first.text.endswith(" "))  # cut at a word boundary, not mid-word
        self.assertEqual(text[first.start : first.end], first.text)

    def test_hard_cut_when_there_is_no_whitespace(self) -> None:
        text = "x" * 100
        chunks = chunk_document("doc.md", text, size=30, overlap=5)
        self.assertEqual(chunks[0].text, "x" * 30)
        self.assertGreater(len(chunks), 1)

    def test_always_makes_progress(self) -> None:
        chunks = chunk_document("doc.md", "abcdefghij" * 10, size=5, overlap=4)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(len({chunk.start for chunk in chunks}), len(chunks))
        self.assertLessEqual(chunks[-1].end, 100)

    def test_empty_and_whitespace_text(self) -> None:
        self.assertEqual(chunk_document("doc.md", ""), [])
        self.assertEqual(chunk_document("doc.md", "   \n\t "), [])

    def test_validation(self) -> None:
        for size, overlap in ((0, 0), (-1, 0), (10, 10), (10, 11), (10, -1)):
            with self.subTest(size=size, overlap=overlap):
                with self.assertRaises(ValueError):
                    chunk_document("doc.md", "text", size=size, overlap=overlap)

    def test_chunk_dataclass(self) -> None:
        chunk = Chunk(doc_id="a.md", start=3, end=8, text="hello")
        self.assertEqual(chunk.length, 5)
        self.assertEqual(chunk.citation, "a.md#chars=3-8")


class TestTokenize(unittest.TestCase):
    def test_lowercases_and_strips_punctuation(self) -> None:
        self.assertEqual(tokenize("The API key MUST rotate!"), ["api", "key", "must", "rotate"])

    def test_keeps_digits_and_underscore_free_words(self) -> None:
        self.assertEqual(tokenize("HTTP/1.1 and 256-bit AES"), ["http", "1", "1", "256", "bit", "aes"])

    def test_stopword_only(self) -> None:
        self.assertEqual(tokenize("the and of it"), [])


class TestRetriever(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = load_docs()
        cls.retriever = Retriever()
        for name, text in cls.docs.items():
            cls.retriever.add_document(name, text)

    def test_index_shape(self) -> None:
        self.assertEqual(self.retriever.document_ids, sorted(self.docs))
        self.assertGreater(self.retriever.chunk_count, 3)

    def test_citations_are_verifiable_for_every_chunk(self) -> None:
        for doc_id, text in self.docs.items():
            for chunk in self.retriever.chunks_for(doc_id):
                self.assertEqual(text[chunk.start : chunk.end], chunk.text)

    def test_rank_prefers_the_relevant_document(self) -> None:
        cases = {
            "how do I rotate an api key": "security.md",
            "when is an invoice prorated for an upgrade": "billing.md",
            "where do I choose a region for my first upload": "onboarding.md",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                hits = self.retriever.search(query, k=1)
                self.assertTrue(hits, f"no hit for {query!r}")
                self.assertEqual(hits[0].chunk.doc_id, expected)

    def test_scores_are_sorted_and_citable(self) -> None:
        hits = self.retriever.search("refund invoice payment method", k=5)
        self.assertTrue(hits)
        scores = [hit.score for hit in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for hit in hits:
            text = self.docs[hit.chunk.doc_id]
            self.assertEqual(text[hit.chunk.start : hit.chunk.end], hit.chunk.text)
            self.assertIn("chars=", hit.citation)

    def test_k_limits_and_zero_returns_nothing(self) -> None:
        self.assertEqual(len(self.retriever.search("api key", k=2)), 2)
        self.assertEqual(self.retriever.search("api key", k=0), [])
        self.assertEqual(self.retriever.search("api key", k=-3), [])

    def test_min_score_filters(self) -> None:
        hits = self.retriever.search("api key", k=10)
        self.assertTrue(hits)
        top = hits[0].score
        filtered = self.retriever.search("api key", k=10, min_score=top * 0.5)
        self.assertTrue(all(hit.score > top * 0.5 for hit in filtered))
        self.assertLessEqual(len(filtered), len(hits))

    def test_queries_without_signal(self) -> None:
        self.assertEqual(self.retriever.search("the and of it"), [])
        self.assertEqual(self.retriever.search("kubernetes helm chart operator"), [])

    def test_duplicate_document_is_rejected(self) -> None:
        retriever = Retriever()
        retriever.add_document("a.md", "some text about keys")
        with self.assertRaises(ValueError):
            retriever.add_document("a.md", "different text")

    def test_empty_doc_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Retriever().add_document("", "text")

    def test_ties_break_deterministically(self) -> None:
        retriever = Retriever()
        retriever.add_document("b.md", "identical text about invoices")
        retriever.add_document("a.md", "identical text about invoices")
        hits = retriever.search("invoices", k=5)
        self.assertEqual([hit.chunk.doc_id for hit in hits], ["a.md", "b.md"])

    def test_add_folder_indexes_markdown_and_text_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "one.md").write_text("alpha beta", encoding="utf-8")
            (folder / "two.txt").write_text("gamma delta", encoding="utf-8")
            (folder / "three.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            retriever = Retriever()
            retriever.add_folder(folder)
            self.assertEqual(retriever.document_ids, ["one.md", "two.txt"])

    def test_empty_document_adds_nothing(self) -> None:
        retriever = Retriever()
        self.assertEqual(retriever.add_document("blank.md", "   "), 0)
        self.assertEqual(retriever.chunk_count, 0)
        self.assertEqual(retriever.search("anything"), [])


class TestRenderAndCli(unittest.TestCase):
    def test_render_without_hits_says_so(self) -> None:
        self.assertIn("no passages matched", render("zzz", []))

    def test_render_lists_citations(self) -> None:
        retriever = Retriever()
        retriever.add_document("security.md", load_docs()["security.md"])
        text = render("rotate an api key", retriever.search("rotate an api key", k=2))
        self.assertIn("security.md#chars=", text)

    def test_cli_prints_hits(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            exit_code = cli.main([str(DOCS_DIR), "--query", "rotate an api key", "--k", "2"])
        self.assertEqual(exit_code, 0)
        output = out.getvalue()
        self.assertIn("indexed", output)
        self.assertIn("security.md#chars=", output)

    def test_cli_errors(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(DOCS_DIR / "missing"), "--query", "x"]), 1)
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([tmp, "--query", "x"]), 1)


if __name__ == "__main__":
    unittest.main()
