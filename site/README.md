# 3D catalogue site

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

## Data comes from the repository, not from the page

`site/data/catalog.js` is **generated** by `scripts/build_catalog.py`, which reads
the front matter of every `problems/**/problem.md` and counts tests, doctests and
solution lines. So the site can never advertise a problem that does not exist, or a
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

19 checks, no GPU required:

- **`layout.test.mjs`** — palette, category grouping, summary totals, galaxy radii and
  sector membership, bar heights/centring/spacing, deterministic star field, keyboard
  index wrapping, filters.
- **`wiring.test.mjs`** — every `THREE.*` symbol exists in the vendored build, every
  scene import is actually exported by `engine.js`, every local import resolves, every
  `getElementById` id exists in `index.html`, and the generated JSON matches the
  problem folders on disk.

## Accessibility & performance notes

- Content is real DOM: headings, links, `<ol>` steps. The canvases are decoration; if
  WebGL is unavailable the page adds `.no-webgl`, hides the canvases and keeps working.
- `prefers-reduced-motion: reduce` renders a single static frame instead of animating.
- Rendering pauses when a canvas scrolls out of view (`IntersectionObserver`), and the
  device pixel ratio is capped at 2.
- Hover-only affordances are duplicated: cards are real links, `Escape` clears a
  selection, filters are `<button aria-pressed>`.

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
