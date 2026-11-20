"""Tests for the file organizer: planning, collisions, moves, manifest, undo."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from solution import cli
from solution.main import (
    DEFAULT_MANIFEST_NAME,
    Move,
    category_for,
    load_manifest,
    organize,
    plan_moves,
    render,
    undo,
    unique_destination,
)


class TempFolderTest(unittest.TestCase):
    """Shared helper: a throwaway directory with files in it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def touch(self, *names: str) -> None:
        for name in names:
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")


class TestCategoryFor(unittest.TestCase):
    def test_known_extensions(self) -> None:
        cases = {
            "holiday.JPG": "Images",
            "scan.png": "Images",
            "report.pdf": "Documents",
            "notes.md": "Documents",
            "backup.tar.gz": "Archives",
            "voice.mp3": "Media",
            "script.py": "Code",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(category_for(Path(name)), expected)

    def test_unknown_and_missing_extension(self) -> None:
        self.assertEqual(category_for(Path("data.xyz")), "Other")
        self.assertEqual(category_for(Path("LICENSE")), "Other")


class TestPlanMoves(TempFolderTest):
    def test_plan_groups_by_type(self) -> None:
        self.touch("a.jpg", "b.pdf", "c.zip", "d.xyz")
        planned = {move.source.name: move.destination for move in plan_moves(self.folder)}
        self.assertEqual(planned["a.jpg"], self.folder / "Images" / "a.jpg")
        self.assertEqual(planned["b.pdf"], self.folder / "Documents" / "b.pdf")
        self.assertEqual(planned["c.zip"], self.folder / "Archives" / "c.zip")
        self.assertEqual(planned["d.xyz"], self.folder / "Other" / "d.xyz")

    def test_plan_is_pure_and_sorted(self) -> None:
        self.touch("z.jpg", "a.jpg", "m.pdf")
        moves = plan_moves(self.folder)
        self.assertEqual([move.source.name for move in moves], ["a.jpg", "m.pdf", "z.jpg"])
        self.assertTrue((self.folder / "z.jpg").exists())  # nothing moved yet
        self.assertFalse((self.folder / "Images").exists())

    def test_subdirectories_are_ignored(self) -> None:
        self.touch("keep/deep.txt", "a.jpg")
        self.assertEqual([move.source.name for move in plan_moves(self.folder)], ["a.jpg"])

    def test_hidden_files_skipped_unless_asked(self) -> None:
        self.touch(".env", ".gitignore", "a.jpg")
        self.assertEqual([move.source.name for move in plan_moves(self.folder)], ["a.jpg"])
        with_hidden = [move.source.name for move in plan_moves(self.folder, include_hidden=True)]
        self.assertEqual(with_hidden, [".env", ".gitignore", "a.jpg"])

    def test_existing_manifest_is_never_moved(self) -> None:
        self.touch(DEFAULT_MANIFEST_NAME, "a.jpg")
        self.assertEqual([move.source.name for move in plan_moves(self.folder)], ["a.jpg"])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlinks_are_skipped(self) -> None:
        target = self.folder / "real.jpg"
        target.write_text("x", encoding="utf-8")
        link = self.folder / "link.jpg"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("cannot create symlink here")
        self.assertEqual([move.source.name for move in plan_moves(self.folder)], ["real.jpg"])

    def test_collision_with_existing_file(self) -> None:
        self.touch("report.pdf")
        self.touch("Documents/report.pdf")
        destination = plan_moves(self.folder)[0].destination
        self.assertEqual(destination.name, "report (1).pdf")

    def test_collision_escalates(self) -> None:
        self.touch("report.pdf", "Documents/report.pdf", "Documents/report (1).pdf")
        destination = plan_moves(self.folder)[0].destination
        self.assertEqual(destination.name, "report (2).pdf")

    def test_missing_folder_raises(self) -> None:
        with self.assertRaises(NotADirectoryError):
            plan_moves(self.folder / "does-not-exist")

    def test_unique_destination_keeps_suffix(self) -> None:
        self.touch("Documents/a.tar.gz")
        # `Path.suffix` only sees ".gz", so the counter lands before it.
        self.assertEqual(
            unique_destination(self.folder / "Documents" / "a.tar.gz", set()).name,
            "a.tar (1).gz",
        )
        self.touch("Documents/photo.jpg")
        self.assertEqual(
            unique_destination(self.folder / "Documents" / "photo.jpg", set()).name,
            "photo (1).jpg",
        )


class TestOrganize(TempFolderTest):
    def test_dry_run_changes_nothing(self) -> None:
        self.touch("a.jpg", "b.pdf")
        moves = organize(self.folder, dry_run=True)
        self.assertEqual(len(moves), 2)
        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertFalse((self.folder / DEFAULT_MANIFEST_NAME).exists())
        self.assertTrue(render(moves, dry_run=True).startswith("would move"))

    def test_organize_moves_files_and_writes_manifest(self) -> None:
        self.touch("a.jpg", "b.pdf", "c.xyz")
        moves = organize(self.folder)
        self.assertEqual(len(moves), 3)
        self.assertTrue((self.folder / "Images" / "a.jpg").exists())
        self.assertTrue((self.folder / "Documents" / "b.pdf").exists())
        self.assertTrue((self.folder / "Other" / "c.xyz").exists())
        self.assertFalse((self.folder / "a.jpg").exists())

        manifest = load_manifest(self.folder / DEFAULT_MANIFEST_NAME)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["folder"], str(self.folder.resolve()))
        self.assertEqual(len(manifest["moves"]), 3)

    def test_second_run_is_a_no_op(self) -> None:
        self.touch("a.jpg")
        organize(self.folder)
        manifest_before = (self.folder / DEFAULT_MANIFEST_NAME).read_text(encoding="utf-8")
        self.assertEqual(organize(self.folder), [])
        self.assertEqual(
            (self.folder / DEFAULT_MANIFEST_NAME).read_text(encoding="utf-8"), manifest_before
        )

    def test_second_run_merges_into_the_manifest(self) -> None:
        self.touch("a.jpg")
        organize(self.folder)
        self.touch("b.pdf")
        organize(self.folder)
        manifest = load_manifest(self.folder / DEFAULT_MANIFEST_NAME)
        self.assertEqual(len(manifest["moves"]), 2)

    def test_custom_manifest_path(self) -> None:
        self.touch("a.jpg")
        custom = self.folder / "log" / "moves.json"
        custom.parent.mkdir()
        organize(self.folder, manifest_path=custom)
        self.assertTrue(custom.exists())
        self.assertFalse((self.folder / DEFAULT_MANIFEST_NAME).exists())


class TestUndo(TempFolderTest):
    def test_undo_restores_names_and_prunes_folders(self) -> None:
        self.touch("a.jpg", "b.pdf")
        organize(self.folder)
        restored = undo(self.folder / DEFAULT_MANIFEST_NAME)
        self.assertEqual(restored, 2)
        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertTrue((self.folder / "b.pdf").exists())
        self.assertFalse((self.folder / "Images").exists())  # pruned
        self.assertFalse((self.folder / DEFAULT_MANIFEST_NAME).exists())

    def test_undo_keeps_folder_that_still_has_files(self) -> None:
        self.touch("a.jpg")
        organize(self.folder)
        self.touch("Images/manual.jpg")
        undo(self.folder / DEFAULT_MANIFEST_NAME)
        self.assertTrue((self.folder / "Images" / "manual.jpg").exists())

    def test_undo_skips_paths_outside_the_folder(self) -> None:
        self.touch("a.jpg")
        organize(self.folder)
        manifest_path = self.folder / DEFAULT_MANIFEST_NAME
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        outside = Path(tempfile.gettempdir()) / "stolen.txt"
        outside.write_text("important", encoding="utf-8")
        data["moves"].append({"source": str(outside), "destination": str(outside)})
        data["moves"].append(
            {"source": "/etc/passwd", "destination": str(self.folder / "Images" / "a.jpg")}
        )
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        restored = undo(manifest_path)
        self.assertEqual(restored, 1)  # only the legitimate move
        self.assertFalse((self.folder / "stolen.txt").exists())

    def test_undo_then_organize_again(self) -> None:
        self.touch("a.jpg")
        organize(self.folder)
        undo(self.folder / DEFAULT_MANIFEST_NAME)
        organize(self.folder)
        self.assertTrue((self.folder / "Images" / "a.jpg").exists())

    def test_undo_collision_does_not_overwrite(self) -> None:
        self.touch("a.jpg")
        organize(self.folder)
        self.touch("a.jpg")  # a new file took the original name
        restored = undo(self.folder / DEFAULT_MANIFEST_NAME)
        self.assertEqual(restored, 0)  # source already exists -> skipped, not overwritten
        self.assertTrue((self.folder / "Images" / "a.jpg").exists())

    def test_broken_manifest(self) -> None:
        bad = self.folder / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            undo(bad)
        wrong = self.folder / "wrong.json"
        wrong.write_text('{"hello": 1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            undo(wrong)


class TestRenderAndCli(TempFolderTest):
    def test_render_empty(self) -> None:
        self.assertIn("nothing to organise", render([], dry_run=False))

    def test_render_lists_moves(self) -> None:
        self.touch("a.jpg")
        text = render(plan_moves(self.folder), dry_run=False)
        self.assertIn("a.jpg -> Images/a.jpg", text)

    def test_cli_dry_run_then_real_run_then_undo(self) -> None:
        self.touch("a.jpg", "b.pdf")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main([str(self.folder), "--dry-run"]), 0)
        self.assertIn("would move (dry run) 2 file(s)", out.getvalue())
        self.assertTrue((self.folder / "a.jpg").exists())

        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([str(self.folder)]), 0)
        self.assertTrue((self.folder / "Images" / "a.jpg").exists())

        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main([str(self.folder), "--undo"]), 0)
        self.assertIn("restored 2 file(s)", out.getvalue())
        self.assertTrue((self.folder / "a.jpg").exists())

    def test_cli_errors(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(self.folder / "nope")]), 1)
            self.assertEqual(cli.main([str(self.folder), "--undo"]), 1)

    def test_cli_include_hidden(self) -> None:
        self.touch(".env")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([str(self.folder), "--include-hidden"]), 0)
        self.assertTrue((self.folder / "Other" / ".env").exists())


class TestMoveDataclass(unittest.TestCase):
    def test_category_property(self) -> None:
        move = Move(Path("/a/x.jpg"), Path("/a/Images/x.jpg"))
        self.assertEqual(move.category, "Images")
        self.assertIn("x.jpg -> Images/x.jpg", str(move))


if __name__ == "__main__":
    unittest.main()
