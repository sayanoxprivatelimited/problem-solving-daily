"""Organise a messy folder by file type — safely, reversibly, and testably.

The rules this module refuses to break:

* **Planning is separate from doing.** ``plan_moves`` is pure (it only reads), so
  ``--dry-run`` shows exactly what ``organize`` will execute.
* **Nothing is ever overwritten.** A name clash becomes ``report (1).pdf``.
* **Every move is recorded** in a JSON manifest, so ``--undo`` can reverse it.
* **A manifest cannot escape its folder.** Paths are resolved and checked, because
  a manifest that has been edited by hand must not become an arbitrary-file-mover.

Example
-------
>>> category_for(Path("holiday.JPG"))
'Images'
>>> category_for(Path("archive.tar.gz"))
'Archives'
>>> category_for(Path("LICENSE"))
'Other'
>>> unique_destination(Path("/nowhere/report.pdf"), {Path("/nowhere/report.pdf")})
PosixPath('/nowhere/report (1).pdf')
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES: dict[str, frozenset[str]] = {
    "Images": frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".heic", ".bmp", ".tiff"}),
    "Documents": frozenset(
        {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".rtf", ".epub"}
    ),
    "Archives": frozenset({".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"}),
    "Media": frozenset({".mp4", ".mkv", ".mov", ".avi", ".mp3", ".wav", ".flac", ".m4a", ".ogg"}),
    "Code": frozenset({".py", ".js", ".ts", ".tsx", ".json", ".html", ".css", ".sh", ".ipynb", ".sql"}),
}
OTHER = "Other"
DEFAULT_MANIFEST_NAME = ".organize-manifest.json"
MANIFEST_VERSION = 1

_CATEGORY_BY_EXTENSION = {
    extension: category for category, extensions in CATEGORIES.items() for extension in extensions
}


@dataclass(frozen=True)
class Move:
    """One planned (or executed) move."""

    source: Path
    destination: Path

    @property
    def category(self) -> str:
        return self.destination.parent.name

    def __str__(self) -> str:  # pragma: no cover - CLI formatting
        return f"{self.source.name} -> {self.destination.parent.name}/{self.destination.name}"


def category_for(path: Path) -> str:
    """Bucket a file by its extension, case-insensitively."""
    suffix = path.suffix.lower()
    return _CATEGORY_BY_EXTENSION.get(suffix, OTHER)


def unique_destination(target: Path, claimed: set[Path]) -> Path:
    """Return a path that neither exists on disk nor collides with `claimed`."""
    if not target.exists() and target not in claimed:
        return target

    stem, suffix, parent = target.stem, target.suffix, target.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists() and candidate not in claimed:
            return candidate
        counter += 1


def plan_moves(folder: str | Path, *, include_hidden: bool = False) -> list[Move]:
    """Compute the moves for `folder` without touching anything."""
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    moves: list[Move] = []
    claimed: set[Path] = set()

    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_file() or entry.is_symlink():
            continue
        if entry.name.startswith(".") and not include_hidden:
            continue
        if entry.name == DEFAULT_MANIFEST_NAME:
            continue

        destination_dir = root / category_for(entry)
        destination = unique_destination(destination_dir / entry.name, claimed)
        claimed.add(destination)
        moves.append(Move(source=entry, destination=destination))

    return moves


def organize(
    folder: str | Path,
    *,
    dry_run: bool = False,
    manifest_path: str | Path | None = None,
    include_hidden: bool = False,
) -> list[Move]:
    """Execute the plan (or just return it when `dry_run`). Writes a manifest."""
    root = Path(folder)
    moves = plan_moves(root, include_hidden=include_hidden)
    if dry_run or not moves:
        return moves

    for move in moves:
        move.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(move.source), str(move.destination))

    manifest = Path(manifest_path) if manifest_path else root / DEFAULT_MANIFEST_NAME

    # Merge with an earlier run so one `--undo` can still reverse everything.
    previous: list[dict] = []
    if manifest.exists():
        try:
            previous = list(load_manifest(manifest)["moves"])
        except (ValueError, json.JSONDecodeError, OSError):
            previous = []

    payload = {
        "version": MANIFEST_VERSION,
        "organized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "folder": str(root.resolve()),
        "moves": previous
        + [
            {"source": str(move.source.resolve()), "destination": str(move.destination.resolve())}
            for move in moves
        ],
    }
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return moves


def load_manifest(manifest_path: str | Path) -> dict:
    """Read and minimally validate a manifest."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("moves"), list):
        raise ValueError(f"not an organize manifest: {manifest_path}")
    return data


def undo(manifest_path: str | Path) -> int:
    """Reverse every move in the manifest. Returns the number of files restored."""
    manifest = Path(manifest_path)
    data = load_manifest(manifest)
    root = Path(data.get("folder") or manifest.parent).resolve()

    restored = 0
    pruned: set[Path] = set()
    for item in reversed(data["moves"]):
        source = Path(item["source"])
        destination = Path(item["destination"])

        # Safety: a hand-edited manifest must not move files from outside the folder.
        if not _is_inside(source, root) or not _is_inside(destination, root):
            continue
        if not destination.exists() or source.exists():
            continue

        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source))
        pruned.add(destination.parent)
        restored += 1

    for directory in sorted(pruned, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()  # only succeeds when empty
        except OSError:
            pass

    if restored:
        manifest.unlink(missing_ok=True)
    return restored


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    return True


def render(moves: list[Move], *, dry_run: bool) -> str:
    """Human-readable plan/output."""
    if not moves:
        return "nothing to organise — folder is already tidy"

    header = "would move (dry run)" if dry_run else "moved"
    lines = [f"{header} {len(moves)} file(s):"]
    width = max(len(str(move)) for move in moves)
    lines += [f"  {move!s:<{width}}" for move in moves]
    return "\n".join(lines)
