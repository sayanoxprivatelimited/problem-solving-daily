---
title: File organizer with undo
category: cli
level: medium
concepts: filesystem safety, dry-run, collision handling, manifests, path traversal
status: solved
---

# File organizer with undo

## Context

Someone's Downloads folder: 900 files, mixed extensions, three copies of
`report.pdf`, and no folders. They want it sorted — but they do **not** want to
find out afterwards that something was mangled.

Automation that touches a user's filesystem has to be boring and reversible:
show the plan first, never overwrite, and be able to put everything back.

## Behaviour

```bash
python -m solution.cli ~/Downloads --dry-run   # show the plan, change nothing
python -m solution.cli ~/Downloads             # do it, write a manifest
python -m solution.cli ~/Downloads --undo      # restore every move
```

Mapping: `Images`, `Documents`, `Archives`, `Media`, `Code`, and `Other` for the
extensions nobody planned for.

## Requirements

| # | Requirement | Why |
|---|-------------|-----|
| 1 | `--dry-run` prints the same plan `organize` would execute, and touches nothing | review before damage |
| 2 | Collisions rename instead of overwrite: `report.pdf` → `report (1).pdf` | silent data loss is the worst bug |
| 3 | A JSON manifest records every move so `--undo` can reverse them | reversibility |
| 4 | Second run does not clobber the manifest from the first run | quitting and re-running is normal |
| 5 | Hidden files (`.env`, `.gitignore`) are left alone unless `--include-hidden` | dotfiles are usually config |
| 6 | Subdirectories are never descended into or moved | stay in scope |
| 7 | `--undo` refuses paths outside the organised folder | a tampered manifest must not move `/etc/passwd` |
| 8 | Emptied category folders are pruned on undo | leave no litter |

## Constraints & acceptance criteria

- Non-existent folder → `NotADirectoryError`, not a silent no-op.
- Symlinks are skipped (documented): following them can move files that live
  outside the folder.
- The plan is deterministic (sorted by file name) so two runs of `--dry-run` are
  identical, and so the tests can assert on it.
- `plan_moves` is pure: it reads the directory but never writes.

## How to run

```bash
cd problems/cli/01-file-organizer
python -m unittest discover -s tests -t .
mkdir -p /tmp/messy && touch /tmp/messy/{a.jpg,b.pdf,note.txt,c.zip}
python -m solution.cli /tmp/messy --dry-run
python -m solution.cli /tmp/messy
python -m solution.cli /tmp/messy --undo
```

## Stretch goals

- `--by-date` mode (`YYYY/MM/`) and a combined `--by-type --by-date` layout.
- Content-based detection for extension-less files (magic bytes).
- Watch mode with `watchdog`, plus a `--trash` option that moves to the OS trash
  instead of `--undo`.
