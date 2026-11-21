"""Tests for cursor pagination: total order, snapshots, tampering and the walk."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from solution import cli
from solution.main import (
    CursorCodec,
    CursorError,
    InvalidCursor,
    Page,
    Row,
    RowStore,
    gather,
    rows_from,
    walk,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ROWS = os.path.join(FIXTURES, "rows.json")


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def store_with(*rows: Row, **kwargs: object) -> RowStore:
    store = RowStore(**kwargs)  # type: ignore[arg-type]
    for row in rows:
        store.add(row)
    return store


class TestRow(unittest.TestCase):
    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            Row("", 1)
        with self.assertRaises(ValueError):
            Row("a", "1")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            Row("a", True)

    def test_position_is_the_total_order(self) -> None:
        self.assertEqual(Row("b", 100).position, (100, "b"))
        self.assertLess(Row("a", 100).position, Row("b", 100).position)
        self.assertLess(Row("z", 99).position, Row("a", 100).position)

    def test_rows_are_immutable(self) -> None:
        row = Row("a", 1)
        with self.assertRaises(Exception):
            row.sort_key = 2  # type: ignore[misc]


class TestOrdering(unittest.TestCase):
    def test_rows_come_back_in_keyset_order(self) -> None:
        store = store_with(Row("c", 2), Row("a", 1), Row("b", 1))
        page = store.page(limit=10)
        self.assertEqual(page.ids, ("a", "b", "c"))

    def test_equal_sort_keys_are_ordered_by_id(self) -> None:
        store = store_with(Row("z", 5), Row("m", 5), Row("a", 5))
        self.assertEqual(store.page(limit=3).ids, ("a", "m", "z"))

    def test_a_full_walk_visits_every_row_exactly_once(self) -> None:
        store = store_with(*[Row(f"r{index:02d}", index % 3) for index in range(20)])
        rows = gather(list(walk(store, limit=6)))
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row.id for row in rows}), 20)
        self.assertEqual([row.position for row in rows], sorted(row.position for row in rows))


class TestPaging(unittest.TestCase):
    def test_page_size_is_respected(self) -> None:
        store = store_with(*[Row(str(index), index) for index in range(5)])
        page = store.page(limit=2)
        self.assertEqual(len(page), 2)
        self.assertTrue(page.has_more)
        self.assertEqual(page.snapshot, 5)

    def test_the_last_page_has_no_cursor(self) -> None:
        store = store_with(Row("a", 1), Row("b", 2))
        page = store.page(limit=10)
        self.assertEqual(page.ids, ("a", "b"))
        self.assertIsNone(page.next_cursor)
        self.assertFalse(page.has_more)

    def test_exactly_full_last_page_has_no_cursor(self) -> None:
        store = store_with(Row("a", 1), Row("b", 2))
        page = store.page(limit=2)
        self.assertEqual(page.ids, ("a", "b"))
        self.assertIsNone(page.next_cursor)

    def test_pages_chain_without_gaps_or_duplicates(self) -> None:
        store = store_with(*[Row(str(index), index % 4) for index in range(11)])
        first = store.page(limit=3)
        second = store.page(cursor=first.next_cursor, limit=3)
        third = store.page(cursor=second.next_cursor, limit=3)
        fourth = store.page(cursor=third.next_cursor, limit=3)
        seen = first.ids + second.ids + third.ids + fourth.ids
        self.assertEqual(len(seen), 11)
        self.assertEqual(len(set(seen)), 11)
        self.assertIsNone(fourth.next_cursor)

    def test_an_empty_store_returns_an_empty_page(self) -> None:
        page = RowStore().page(limit=5)
        self.assertEqual(page.rows, ())
        self.assertIsNone(page.next_cursor)

    def test_limit_validation(self) -> None:
        store = store_with(Row("a", 1))
        for bad in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                store.page(limit=bad)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            store_with(Row("a", 1), max_page=5).page(limit=6)


class TestMutation(unittest.TestCase):
    def test_an_insert_during_the_walk_is_invisible(self) -> None:
        store = store_with(Row("b", 2), Row("c", 3))
        first = store.page(limit=1)
        store.add(Row("a", 1))  # sorts before everything already read
        rest = store.page(cursor=first.next_cursor, limit=10)
        self.assertEqual(first.ids + rest.ids, ("b", "c"))
        self.assertNotIn("a", rest.ids)

    def test_an_insert_after_the_last_page_does_not_appear(self) -> None:
        store = store_with(Row("a", 1), Row("b", 2))
        page = store.page(limit=2)
        store.add(Row("c", 3))
        self.assertEqual(page.ids, ("a", "b"))
        self.assertIsNone(page.next_cursor)

    def test_a_delete_during_the_walk_shortens_and_never_duplicates(self) -> None:
        store = store_with(*[Row(str(index), index) for index in range(6)])
        first = store.page(limit=2)
        store.delete("3")
        rows = gather([first] + list(store.walk(limit=2, cursor=first.next_cursor)))
        ids = [row.id for row in rows]
        self.assertNotIn("3", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 5)

    def test_deleting_the_next_row_does_not_repeat_the_cursor_position(self) -> None:
        store = store_with(Row("a", 1), Row("b", 2), Row("c", 3))
        first = store.page(limit=1)
        store.delete("b")
        second = store.page(cursor=first.next_cursor, limit=1)
        self.assertEqual(second.ids, ("c",))

    def test_duplicate_ids_are_refused(self) -> None:
        store = store_with(Row("a", 1))
        with self.assertRaises(ValueError):
            store.add(Row("a", 2))

    def test_delete_reports_whether_it_did_anything(self) -> None:
        store = store_with(Row("a", 1))
        self.assertTrue(store.delete("a"))
        self.assertFalse(store.delete("a"))
        self.assertEqual(len(store), 0)


class TestCursorCodec(unittest.TestCase):
    def test_round_trip(self) -> None:
        codec = CursorCodec(b"secret")
        token = codec.encode((100, "b"), snapshot=7)
        self.assertEqual(codec.decode(token), ((100, "b"), 7))

    def test_tokens_are_url_safe(self) -> None:
        token = CursorCodec(b"secret").encode((1, "a"), snapshot=1)
        self.assertTrue(
            all(character.isalnum() or character in "-_." for character in token), token
        )

    def test_a_tampered_token_is_refused(self) -> None:
        codec = CursorCodec(b"secret")
        token = codec.encode((1, "a"), snapshot=1)
        with self.assertRaises(InvalidCursor):
            codec.decode(token[:-2] + ("aa" if not token.endswith("aa") else "bb"))

    def test_another_secret_is_refused(self) -> None:
        token = CursorCodec(b"one").encode((1, "a"), 1)
        with self.assertRaises(InvalidCursor) as caught:
            CursorCodec(b"two").decode(token)
        self.assertIn("signature", str(caught.exception))

    def test_the_version_is_checked(self) -> None:
        token = CursorCodec(b"secret").encode((1, "a"), 1)
        with self.assertRaises(InvalidCursor) as caught:
            CursorCodec(b"secret", version=2).decode(token)
        self.assertIn("unsupported cursor version", str(caught.exception))

    def test_malformed_tokens_are_refused(self) -> None:
        codec = CursorCodec(b"secret")
        for bad in ("", "nodot", "a.b.c", "!!!.xxx"):
            with self.assertRaises(InvalidCursor):
                codec.decode(bad)

    def test_a_signed_but_wrong_shape_payload_is_refused(self) -> None:
        codec = CursorCodec(b"secret")
        forged = codec._sign(b'{"v":1,"s":"x","k":1,"i":"a"}')
        import base64

        token = base64.urlsafe_b64encode(b'{"v":1,"s":"x","k":1,"i":"a"}').decode().rstrip("=")
        with self.assertRaises(InvalidCursor):
            codec.decode(f"{token}.{forged}")

    def test_an_empty_secret_is_an_error(self) -> None:
        with self.assertRaises(CursorError):
            CursorCodec(b"")


class TestPage(unittest.TestCase):
    def test_page_helpers(self) -> None:
        page = Page(rows=(Row("a", 1), Row("b", 2)), next_cursor="token", snapshot=2)
        self.assertEqual(len(page), 2)
        self.assertTrue(page.has_more)
        self.assertEqual(page.ids, ("a", "b"))


class TestRowParsing(unittest.TestCase):
    def test_list_and_wrapper_forms(self) -> None:
        rows = rows_from([{"id": "a", "sort_key": 1}])
        self.assertEqual(rows, [Row("a", 1, "")])
        self.assertEqual(len(rows_from({"rows": [{"id": "a", "sort_key": 1}]})), 1)

    def test_bad_shapes(self) -> None:
        with self.assertRaises(ValueError):
            rows_from("nope")
        with self.assertRaises(ValueError):
            rows_from([["a"]])
        with self.assertRaises(ValueError):
            rows_from([{"id": "a"}])


class TestCli(unittest.TestCase):
    def test_one_page(self) -> None:
        code, out, _ = run_cli(ROWS, "--page-size", "2")
        self.assertEqual(code, 0)
        self.assertIn("page 1: 2 row(s) [inv-1, inv-3]", out)

    def test_the_walk_prints_every_page(self) -> None:
        code, out, err = run_cli(ROWS, "--page-size", "2", "--walk")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("page "), 3)
        self.assertIn("5 row(s)", err)

    def test_json_walk(self) -> None:
        code, out, _ = run_cli(ROWS, "--page-size", "2", "--walk", "--json")
        self.assertEqual(code, 0)
        pages = json.loads(out)
        self.assertEqual(len(pages), 3)
        self.assertEqual(sum(len(page["rows"]) for page in pages), 5)
        self.assertIsNone(pages[-1]["next_cursor"])

    def test_a_tampered_cursor_exits_one(self) -> None:
        code, _, err = run_cli(ROWS, "--cursor", "not-a-cursor")
        self.assertEqual(code, 1)
        self.assertIn("error:", err)

    def test_bad_input_exits_two(self) -> None:
        code, _, err = run_cli(os.path.join(FIXTURES, "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        code, _, _ = run_cli(ROWS, "--page-size", "0")
        self.assertEqual(code, 2)

    def test_jsonl_input(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write('{"id": "a", "sort_key": 1}\n{"id": "b", "sort_key": 2}\n')
            path = handle.name
        try:
            code, out, _ = run_cli(path, "--page-size", "5", "--json")
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        self.assertEqual([row["id"] for row in json.loads(out)[0]["rows"]], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
