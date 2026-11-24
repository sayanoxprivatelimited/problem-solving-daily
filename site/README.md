# Sayanox Problem Solve Daily — the 3D site

A static, dependency-free showcase for the repository: **four 3D scenes**, no build
step, no bundler, no CDN. Three.js is vendored (`assets/three.module.js`, MIT) so the
page works offline and cannot be broken by a CDN outage.

```bash
python3 -m http.server 8000 --directory site    # from the repo root
# open http://localhost:8000
```

The page needs to be served over HTTP (ES modules and `fetch` do not work from
`file://`). Any static server works; `make serve` runs the command above.

## The four scenes

| Scene | Where | What it does |
|---|---|---|
| **Token stream** | hero | 260 instanced cubes fly down a tunnel, coloured by category, camera parallaxes with the pointer |
| **Catalogue galaxy** | `#catalog` | one node per problem in a category-sectorised spiral; drag to orbit, hover for a tooltip, click to select the card, filtering dims whole sectors |
| **Test coverage bars** | `#numbers` | 3D bar chart of tests per problem, height from `barLayout`, drag to spin, hover for the title |
| **Structure** | `#how` | slow wireframe icosahedron with the three workflow labels pinned around it |

## What the page does

| Interaction | Where | Notes |
| --- | --- | --- |
| Search | catalogue header | every whitespace-separated token must appear in the title, category, level, concepts, summary or rules |
| Category filter | catalogue header | dims the matching sector of the galaxy |
| Details | each card | a `<dialog>` sheet: summary, the first four rules from the brief, links to `problem.md` and `solution/main.py` |
| Hover / click a node | galaxy | tooltip, then selects the matching card |
| Keyboard | anywhere | `j` / `↓` and `k` / `↑` walk the visible cards, `Esc` clears the selection and closes the sheet |
| Theme | navigation bar | light / dark, remembered in `localStorage`, applied before first paint by an inline script |
| Deep links | address bar | `?q=tokenizer` pre-fills the search, `?p=01-bpe-tokenizer` opens that problem's sheet, `#problem-<slug>` selects its card. Written with `replaceState`, so searching does not fill the back button |
| Share | footer, hero, detail sheet | X, LinkedIn, WhatsApp, Telegram, Reddit and a copy-link button; the hero button uses `navigator.share` on phones. The static markup already points at the live URL, so links work with JavaScript off |

## Data comes from the repository, not from the page

`site/data/catalog.js` is **generated** by `scripts/build_catalog.py`, which reads
the front matter of every `problems/**/problem.md`, counts tests, doctests and
solution lines, and lifts two pieces of prose out of the brief: `summary` (first
paragraph of *Context*) and `criteria` (the first four rows of *Requirements*, or
of *Business rules* / *Constraints & acceptance criteria* when a brief uses those
headings instead). So the site can never advertise a problem that does not exist, or a
test count that is wrong:

```bash
python scripts/build_catalog.py            # refresh catalog.md + site data
python scripts/build_catalog.py --check    # CI: fail if either is stale
```

The page imports the module — no `fetch`, no loading state, and the site also works
opened straight from disk. `site/tests/wiring.test.mjs` checks that the module mirrors
`problems.json` and that both match the folders on disk.

## Layout

```
site/
├── index.html
├── css/style.css          # design tokens, grid, cards, tooltip, responsive
├── js/
│   ├── main.js            # fetch data → render DOM → boot scenes
│   ├── layout.js          # pure maths: palette, galaxy spiral, bar heights, stars
│   ├── engine.js          # Stage: renderer, resize, visibility, reduced motion
│   └── scenes/
│       ├── hero.js        # instanced token stream
│       ├── galaxy.js      # catalogue nodes + raycasting + drag orbit
│       ├── bars.js        # 3D bar chart
│       └── structure.js   # wireframe divider
├── data/catalog.js        # generated ES module the page imports
├── data/problems.json     # the same payload, for tooling
├── assets/three.module.js # vendored Three.js r169 (MIT)
└── tests/                 # node --test
```

## Tests

```bash
node --test site/tests/*.test.mjs     # or: make site-test
```

40 checks, no GPU required:

- **`layout.test.mjs`** — palette, category grouping, summary totals, galaxy radii and
  sector membership, bar heights/centring/spacing, deterministic star field, keyboard
  index wrapping, filters.
- **`wiring.test.mjs`** — every `THREE.*` symbol exists in the vendored build, every
  scene import is actually exported by `engine.js`, every local import resolves, every
  `getElementById` id exists in `index.html`, and the generated JSON matches the
  problem folders on disk.
- **`seo.test.mjs`** — title and description lengths, canonical/OG/sitemap agreement,
  the 1200×630 preview read straight out of the JPEG header, robots.txt, one parseable
  JSON-LD graph whose 12 items match `problems.json`, share targets that work without
  JavaScript, and `build_seo.py --check` so a stale sitemap cannot be committed.

There is also a jsdom smoke test kept outside the repo (it needs `jsdom` installed):
it loads the page, stubs the `IntersectionObserver` so no WebGL starts, imports
`js/main.js` for real and asserts 28 behaviours — rendering, search, the URL sync, the
detail sheet, `?q=` / `?p=` / `#problem-` deep links and the share targets.

## SEO & sharing

Nothing a crawler needs is generated at runtime, so `index.html` carries the title,
description, keywords, canonical, Open Graph and Twitter tags, and a schema.org graph
(`WebSite`, `Organization`, `CollectionPage`, `ItemList` of 12 `LearningResource`s with
their concepts, level and deep link). Three files are generated by
`scripts/build_seo.py` from the same `problems.json` the page renders:

| File | What it is |
| --- | --- |
| `site/robots.txt` | `Allow: /` plus the sitemap location |
| `site/sitemap.xml` | the one canonical URL (`?p=` links canonicalise to it) |
| the JSON-LD block in `site/index.html` | the structured-data graph, between `seo:jsonld:start` / `seo:jsonld:end` |
| the `<meta name="google-site-verification">` token | hand-written in `index.html`: Google checks it byte for byte, so a test asserts it verbatim |

```bash
python scripts/build_seo.py            # rewrite them
python scripts/build_seo.py --check    # fail if any is stale
```

Images: `assets/og-cover.jpg` is the 1200×630 preview (70 KB), `assets/icon.svg` the
favicon and `assets/apple-touch-icon.png` the 180×180 touch icon. The deployed shell
rewrites all three to absolute CDN URLs, because a link preview is fetched by a
crawler that has no page to resolve relative paths against.

## Accessibility & performance notes

The page is built so that a slow phone can still read it:

- **Scenes boot on approach, not on load.** Each canvas gets its own
  `IntersectionObserver` with a 400px `rootMargin`; until then its module (and
  Three.js itself) is not downloaded and no WebGL context exists. One context at
  first paint instead of four.
- **No allocations in the animation loop.** Scale lerps and label projections reuse a
  scratch `Vector3`; cursor writes are de-duplicated before touching `style`, which
  otherwise invalidates style every frame.
- **Hidden tabs cost nothing.** The stage cancels its `requestAnimationFrame` on
  `visibilitychange` and resumes when the tab is back.
- **Pixel ratio is capped at 1.75.** A 3x retina canvas would triple the fragment work
  for a gradient-heavy scene you cannot tell apart.
- **Off-screen canvases stop rendering** (the stage pauses when its canvas leaves the
  viewport) and `prefers-reduced-motion` renders a single frame instead of a loop.
- **Content first.** Cards, filters, the detail sheet, search and the theme are plain
  DOM; when WebGL is missing the canvases are hidden and everything else still works.
- **Keyboard and screen readers:** skip link, labelled controls, `aria-pressed` on the
  filters and theme button, `role="status"` on the result count and tooltip, focus
  returned to the card after the sheet closes.


## Vendored dependency

`assets/three.module.js` is Three.js r169, MIT licensed — see
`assets/three-LICENSE.txt`. It is committed on purpose: no CDN, no `npm install`,
no supply-chain surprise.

Every module imports the bare specifier `three`, and `index.html` resolves it with
an import map:

```html
<script type="importmap">
  { "imports": { "three": "./assets/three.module.js" } }
</script>
```

That single value is the seam between the two ways the site runs:

| Where | How `three` resolves | Why |
| --- | --- | --- |
| `make serve` | `./assets/three.module.js` | offline, pinned bytes, no network |
| `.site-dist/` | `https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.js` | the upload bundle stays 73 KB instead of 1.4 MB |

`python3 scripts/build_site_deploy.py` (or `make site-bundle`) writes that bundle
to `.site-dist/`, copying the sources byte for byte and rewriting the import map
plus the preload hint. The bundle is an artifact — `.site-dist/` is git-ignored,
and `site/` remains the source of truth.

Because the scenes are imported lazily, a failed `three` request costs the four
canvases and nothing else: the cards, filters, stats and footer are already in the
DOM, and the page switches to its `no-webgl` styles.

## Features beyond the catalogue

| Feature | How it works | Where |
| --- | --- | --- |
| Command palette | `⌘K` / `Ctrl-K` (or the nav button, or `/`) opens a dialog that ranks problems, category filters and actions with `fuzzyScore` + `rankCommands` | `js/palette.js`, `js/layout.js` |
| Local progress | Marking a problem solved writes a sorted array of slugs to `localStorage` under `psd-solved`; the hero meter, the card badges and the "unsolved only" chip all read from it | `js/progress.js` |
| Related problems | The detail sheet lists up to three neighbours: a shared concept scores 2, the same category scores 1, so the graph is never empty | `relatedProblems()` in `js/layout.js` |
| Offline support | A service worker precaches the shell, serves navigation network-first (a deploy is visible on the next load) and everything else stale-while-revalidate; `offline.html` is the fallback | `sw.js`, `assets/manifest.webmanifest` |
| Installable | A web app manifest with a standalone display mode, theme colour and two icons | `assets/manifest.webmanifest` |

All of it is enhancement-only: with JavaScript disabled, a blocked service worker
or a full `localStorage`, the page is still the catalogue it was before.

## Tests

```bash
node --test site/tests/*.test.mjs    # 77 pure-function tests, no dependencies
npm install jsdom --no-save          # optional, for the DOM smoke test
node tools/smoke-dom.mjs             # 17 checks: palette, progress, filters, sheet
```

## Deployment

Live at **https://problem-solving-daily.vercel.app** (Vercel project
`problem-solving-daily`, hobby plan).

The project is **connected to this repository** and its root directory is `site/`,
so a deployment is a clone followed by nothing at all: `site/vercel.json` says
`framework: null`, no build command, no install step, `outputDirectory: "."`. The
files that were cloned *are* the site — same origin for the CSS, the scripts, the
data and the 1.4 MB Three.js build, so there is no CDN pin to keep in sync.

```bash
git push origin main          # merge a PR
# then one deployment, from the Vercel app:
#   create_deployment(project=problem-solving-daily, git_source={type: github,
#                     ref: main, repoId: 1465007432}, target: production)
```

Verification is by content, not by faith: `python3 scripts/build_site_deploy.py`
still writes the exact bytes that are served (`.site-dist/` for the same-origin
bundle), so `curl -s https://problem-solving-daily.vercel.app/ | sha1sum` can be
compared with the file in the repository.

`scripts/build_site_deploy.py` also keeps two upload-shaped artifacts as a fallback
for channels that need them:

| Target | Command | Contents |
| --- | --- | --- |
| Same-origin bundle | `make site-bundle` | `.site-dist/` — the 15 files Vercel serves, import map unchanged |
| Upload API with a small inline limit | `make site-shell REF=<commit-sha>` | `.site-dist-remote/index.html` — one ~52 KB file that loads the CSS, scripts, icons and preview image from jsDelivr's GitHub mirror at that commit |

The shell is the older path, kept working on purpose: it pins the page to a commit
and needs three files (`index.html`, `robots.txt`, `sitemap.xml`) in one upload. The
git deployment makes it unnecessary, but a file-upload fallback is a useful thing to
have when a repository connection breaks.
