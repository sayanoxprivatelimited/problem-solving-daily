#!/usr/bin/env python3
"""Build the upload bundle for the 3D site.

The site is committed as a static folder: every script imports the bare specifier
``three``, and ``site/index.html`` resolves it through an import map to the
vendored build in ``site/assets/``. That copy is 1.3 MB of untouched upstream
JavaScript, so it is deliberately *not* part of the deploy bundle: uploading it
through a JSON API means inlining the whole file, which is wasteful and slow.

Instead this script copies the sources into ``.site-dist/`` and rewrites that one
import map value to a pinned CDN build of the same version. Everything else —
markup, styles, scenes, the generated catalogue data — ships byte for byte.

    python3 scripts/build_site_deploy.py            # build .site-dist/
    python3 scripts/build_site_deploy.py --print    # build and list the payload

The bundle is an artifact, not source: ``.site-dist/`` is git-ignored, and the
committed ``site/`` folder remains the single source of truth.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DIST = ROOT / ".site-dist"

#: Files that belong in the browser-facing bundle, relative to ``site/``.
PAYLOAD = (
    "index.html",
    "css/style.css",
    "js/main.js",
    "js/layout.js",
    "js/engine.js",
    "js/scenes/hero.js",
    "js/scenes/galaxy.js",
    "js/scenes/bars.js",
    "js/scenes/structure.js",
    "data/problems.json",
)

#: Same version as the vendored build, so local and deployed behaviour match.
THREE_VERSION = "0.169.0"
CDN_URL = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js"

LOCAL_MAP = '"three": "./assets/three.module.js"'
REMOTE_MAP = f'"three": "{CDN_URL}"'


def vendored_version() -> str:
    """Read the revision banner out of the vendored file, e.g. ``'169'``."""
    header = (SITE / "assets/three.module.js").read_text(encoding="utf-8")[:4000]
    for line in header.splitlines():
        if "THREE.WebGLRenderer" in line and "@license" in line:
            continue
        marker = "const REVISION = '"
        if marker in line:
            return line.split(marker, 1)[1].split("'", 1)[0]
    raise SystemExit("could not read REVISION from site/assets/three.module.js")


def build() -> list[Path]:
    if DIST.exists():
        shutil.rmtree(DIST)

    written: list[Path] = []
    for relative in PAYLOAD:
        source = SITE / relative
        if not source.is_file():
            raise SystemExit(f"missing source file: site/{relative}")
        target = DIST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        written.append(target)

    index = DIST / "index.html"
    html = index.read_text(encoding="utf-8")
    if LOCAL_MAP not in html:
        raise SystemExit("index.html no longer contains the expected import map entry")

    # The preload hint has to follow the import map, otherwise the browser fetches
    # a file the bundle does not contain.
    html = html.replace(
        '    <link rel="preload" href="assets/three.module.js" as="script" crossorigin="anonymous" />',
        '    <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin="anonymous" />',
    ).replace(LOCAL_MAP, REMOTE_MAP)
    index.write_text(html, encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--print",
        dest="show_payload",
        action="store_true",
        help="list the bundle files (path and size) after building",
    )
    args = parser.parse_args(argv)

    revision = vendored_version()
    if f"three@{THREE_VERSION}" not in CDN_URL or revision != "169":
        print(f"note: vendored Three.js is r{revision}, bundle pins {THREE_VERSION}", file=sys.stderr)

    written = build()
    total = sum(path.stat().st_size for path in written)
    print(f"built .site-dist/ — {len(written)} files, {total / 1024:.1f} KB")

    if args.show_payload:
        for path in written:
            print(f"  {path.relative_to(DIST).as_posix():<24} {path.stat().st_size:>7} B")
        print(f"  -> three {revision} from {CDN_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
