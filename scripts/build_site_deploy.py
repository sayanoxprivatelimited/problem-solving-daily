#!/usr/bin/env python3
"""Build the deployable bundles for the 3D site.

The site is deployed from git (Vercel root directory `site/`, no build step), so
`site/` is what production serves. This script exists for the two cases where a
directory is not enough: a same-origin bundle with the import map untouched
(`build()`), and the single-file shell for upload channels (`--remote-ref`).

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
    python3 scripts/build_site_deploy.py --remote-ref <sha>   # single-file shell

`--remote-ref` exists for upload channels that only accept small inline payloads:
it writes `.site-dist-remote/index.html`, a shell whose stylesheet and module entry
point load from jsDelivr's GitHub mirror at that commit. Pin a commit SHA, never a
branch, so the deployed page cannot change under you.

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
REMOTE_DIST = ROOT / ".site-dist-remote"

REPOSITORY = "sayanoxprivatelimited/problem-solving-daily"

#: Canonical origin, matching SITE_URL in scripts/build_seo.py.
SITE_URL = "https://problem-solving-daily.vercel.app"

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
    "data/catalog.js",
    "robots.txt",
    "sitemap.xml",
    "offline.html",
    "sw.js",
    "assets/icon.svg",
    "assets/apple-touch-icon.png",
    "assets/og-cover.jpg",
    "assets/manifest.webmanifest",
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


def build_remote(ref: str) -> Path:
    """Write a one-file shell that loads the site from the repository's CDN mirror."""
    if not ref or "/" in ref or ref.startswith("-"):
        raise SystemExit(f"not a commit SHA or branch: {ref!r}")

    base = f"https://cdn.jsdelivr.net/gh/{REPOSITORY}@{ref}/site"
    html = (SITE / "index.html").read_text(encoding="utf-8")

    for local, remote in (
        ('href="css/style.css"', f'href="{base}/css/style.css"'),
        ('src="js/main.js"', f'src="{base}/js/main.js"'),
        ('href="assets/icon.svg"', f'href="{base}/assets/icon.svg"'),
        (
            'href="assets/apple-touch-icon.png"',
            f'href="{base}/assets/apple-touch-icon.png"',
        ),
        # The shell is the only file uploaded, so the sitemap is announced on the
        # deployment's own domain rather than relative to a CDN path.
        ('href="sitemap.xml"', f'href="{SITE_URL}/sitemap.xml"'),
    ):
        if local not in html:
            raise SystemExit(f"index.html no longer contains: {local}")
        html = html.replace(local, remote)

    html = html.replace(LOCAL_MAP, REMOTE_MAP)

    # The same preload swap the local bundle does, plus a guard: nothing may point
    # at a file this one-file deployment does not contain.
    html = html.replace(
        '    <link rel="preload" href="assets/three.module.js" as="script" crossorigin="anonymous" />',
        '    <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin="anonymous" />',
    )
    for stale in (
        'href="css/',
        'src="js/',
        'href="assets/',
        'content="assets/',
        'href="sitemap.xml"',
        'assets/three.module.js',
    ):
        if stale in html:
            raise SystemExit(f"remote shell still references a local file: {stale}")

    REMOTE_DIST.mkdir(parents=True, exist_ok=True)
    index = REMOTE_DIST / "index.html"
    index.write_text(html, encoding="utf-8")
    print(f"built .site-dist-remote/index.html — {index.stat().st_size} B, assets at {base}")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--print",
        dest="show_payload",
        action="store_true",
        help="list the bundle files (path and size) after building",
    )
    parser.add_argument(
        "--remote-ref",
        metavar="REF",
        help="only build the single-file shell that loads assets from jsDelivr at REF",
    )
    args = parser.parse_args(argv)

    if args.remote_ref:
        vendored_version()
        build_remote(args.remote_ref)
        return 0

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
