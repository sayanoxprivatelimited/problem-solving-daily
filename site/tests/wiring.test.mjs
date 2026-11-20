/**
 * Static wiring checks — fast, deterministic, no GPU required.
 *
 * A 3D page has three ways to break that unit tests usually miss:
 *   1. a `THREE.Something` that does not exist in the vendored build,
 *   2. a scene importing a helper that `engine.js` never exported,
 *   3. `getElementById('x')` where the HTML has no `#x`.
 *
 * All three are typos, and all three are cheap to catch by reading the sources.
 *
 *   node --test site/tests/*.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const read = (relative) => fs.readFileSync(path.join(SITE, relative), 'utf8');

function walk(directory) {
  return fs
    .readdirSync(path.join(SITE, directory), { withFileTypes: true })
    .flatMap((entry) => {
      const relative = path.join(directory, entry.name);
      return entry.isDirectory() ? walk(relative) : [relative];
    });
}

const scriptFiles = walk('js').filter((file) => file.endsWith('.js'));
const sources = new Map(scriptFiles.map((file) => [file, read(file)]));

test('the vendored Three.js build exports every symbol the scenes use', () => {
  const threeSource = read('assets/three.module.js');
  const exportBlock = threeSource.match(/export \{([^}]+)\};?\s*$/);
  assert.ok(exportBlock, 'could not find the export block in three.module.js');

  const exported = new Set(
    exportBlock[1]
      .split(',')
      .map((entry) => entry.trim().split(/\s+as\s+/).pop())
      .filter(Boolean),
  );
  assert.ok(exported.has('WebGLRenderer'));
  assert.ok(exported.has('InstancedMesh'));

  const used = new Map();
  for (const [file, source] of sources) {
    for (const match of source.matchAll(/THREE\.([A-Za-z0-9_]+)/g)) {
      if (!used.has(match[1])) used.set(match[1], file);
    }
  }
  assert.ok(used.size > 15, `expected the scenes to use the Three.js API, saw ${used.size} symbols`);

  const missing = [...used].filter(([symbol]) => !exported.has(symbol));
  assert.deepEqual(missing, [], `unknown Three.js symbols: ${JSON.stringify(missing)}`);
});

test('scenes only import helpers that engine.js exports', () => {
  const engineExports = new Set(
    [...read('js/engine.js').matchAll(/export (?:class|function|const) (\w+)/g)].map(
      (match) => match[1],
    ),
  );
  assert.ok(engineExports.has('Stage'));

  const sceneFiles = scriptFiles.filter((file) => file.startsWith(path.join('js', 'scenes')));
  assert.equal(sceneFiles.length, 4, 'expected four scenes');

  for (const file of sceneFiles) {
    for (const match of sources.get(file).matchAll(/import \{([^}]+)\} from '\.\.\/engine\.js'/g)) {
      const names = match[1].split(',').map((name) => name.trim()).filter(Boolean);
      for (const name of names) {
        assert.ok(engineExports.has(name), `${file} imports ${name}, which engine.js does not export`);
      }
    }
  }
});

test('every local import points at a file that exists', () => {
  for (const [file, source] of sources) {
    for (const match of source.matchAll(/from '(\.[^']+)'|import\('(\.[^']+)'\)/g)) {
      const specifier = match[1] ?? match[2];
      const target = path.resolve(SITE, path.dirname(file), specifier);
      assert.ok(fs.existsSync(target), `${file} imports missing file ${specifier}`);
    }
  }
});

test('scenes load lazily so missing WebGL never blanks the page', () => {
  const main = read('js/main.js');
  assert.doesNotMatch(
    main,
    /^import .*from '\.\/scenes\//m,
    'main.js must not statically import a scene: Three.js would gate first paint',
  );
  for (const scene of ['hero', 'galaxy', 'bars', 'structure']) {
    assert.match(main, new RegExp(`import\\('\\.\\/scenes\\/${scene}\\.js'\\)`));
  }
  assert.match(main, /await Promise\.all\(/);
});

test('the import map is the single switch for where Three.js comes from', () => {
  const html = read('index.html');
  const map = html.match(/<script type="importmap">([\s\S]*?)<\/script>/);
  assert.ok(map, 'index.html must declare an import map');

  const imports = JSON.parse(map[1]).imports;
  assert.ok(imports.three, 'the import map must resolve the bare specifier "three"');

  // Local development points at the vendored build; scripts/build_site_deploy.py
  // rewrites this one value to a pinned CDN copy for the Vercel upload.
  const vendored = path.resolve(SITE, imports.three);
  assert.ok(fs.existsSync(vendored), `import map points at a missing file: ${imports.three}`);

  for (const [file, source] of sources) {
    assert.doesNotMatch(
      source,
      /from '(\.[^']*\/)?assets\/three\.module\.js'/,
      `${file} must import 'three', not the asset path directly`,
    );
  }
  const threeUsers = [...sources.values()].filter((source) => /from 'three'/.test(source));
  assert.equal(threeUsers.length, 5, 'engine.js and all four scenes share one Three.js instance');
});

test('every DOM id used by main.js exists in index.html', () => {
  const html = read('index.html');
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
  const mainSource = read('js/main.js');

  const referenced = [...mainSource.matchAll(/getElementById\('([^']+)'\)/g)].map((match) => match[1]);
  assert.ok(referenced.length >= 6, 'expected main.js to look up several elements');
  const missing = referenced.filter((id) => !ids.has(id));
  assert.deepEqual(missing, [], `main.js references missing ids: ${missing.join(', ')}`);
});

test('the page ships the search, theme and detail controls it wires up', () => {
  const html = read('index.html');
  for (const id of [
    'search',
    'result-count',
    'theme-toggle',
    'detail',
    'detail-title',
    'detail-criteria',
    'detail-links',
    'detail-close',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `index.html is missing #${id}`);
  }
  assert.match(html, /<dialog[^>]*id="detail"/, 'the detail sheet must be a dialog');
  // The stored theme has to be applied before first paint, or light users flash dark.
  assert.match(html, /localStorage\.getItem\('psd-theme'\)/);
  assert.match(read('js/main.js'), /localStorage\.setItem\(THEME_KEY/);
});

test('every problem carries the summary and rules the sheet renders', () => {
  const data = JSON.parse(read('data/problems.json'));
  for (const problem of data.problems) {
    assert.ok(problem.summary?.length > 20, `${problem.slug} has no summary`);
    assert.ok(problem.criteria.length > 0, `${problem.slug} has no criteria`);
    assert.ok(
      problem.criteria.every((rule) => rule.length <= 140),
      `${problem.slug} has a rule that is too long for the sheet`,
    );
  }
});

test('index.html loads the module entry point and the stylesheet', () => {
  const html = read('index.html');
  assert.match(html, /<script type="module" src="js\/main\.js"><\/script>/);
  assert.match(html, /<link rel="stylesheet" href="css\/style\.css" \/>/);
  assert.match(html, /<canvas id="hero-canvas"/);
  assert.match(html, /<canvas id="galaxy-canvas"/);
  assert.match(html, /<canvas id="bars-canvas"/);
  assert.match(html, /<canvas id="structure-canvas"/);
});

test('the generated data module matches the JSON and the problems on disk', () => {
  const module = read('data/catalog.js');
  assert.match(module, /^\/\/ Generated by scripts\/build_catalog\.py/);
  assert.match(module, /^export default \{/m);

  // Everything between `export default ` and the final `;` is plain JSON.
  const body = module.slice(module.indexOf('export default ') + 'export default '.length).trim();
  assert.ok(body.endsWith(';'));
  const data = JSON.parse(body.slice(0, -1));
  assert.deepEqual(data, JSON.parse(read('data/problems.json')), 'catalog.js must mirror problems.json');

  const problemsDir = path.resolve(SITE, '..', 'problems');
  const onDisk = fs
    .readdirSync(problemsDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((category) =>
      fs
        .readdirSync(path.join(problemsDir, category.name), { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => `${category.name}/${entry.name}`),
    );

  assert.equal(data.problems.length, onDisk.length, 'run: python scripts/build_catalog.py');
  assert.deepEqual(
    data.problems.map((problem) => problem.path.replace(/^problems\//, '')).sort(),
    onDisk.sort(),
  );

  const tests = data.problems.reduce((total, problem) => total + problem.tests, 0);
  assert.equal(tests, data.stats.tests, 'stats.tests must equal the sum of per-problem counts');
  assert.ok(data.problems.every((problem) => problem.brief_url.includes('/problem.md')));
  assert.ok(data.problems.every((problem) => problem.concepts.length > 0));
});
