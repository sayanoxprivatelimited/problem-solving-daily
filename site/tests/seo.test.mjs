/**
 * SEO and share checks — the parts a crawler sees before any scene boots.
 *
 * Everything here is generated (`scripts/build_seo.py`) or hand-written once in
 * `index.html`, so the risk is drift: a renamed problem, a deleted asset, a
 * description that grows past the snippet, a canonical URL that disagrees with
 * the sitemap. Each of those is a silent ranking loss in production, and each of
 * them is cheap to catch by reading the files.
 *
 *   node --test site/tests/*.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, '..');
const ROOT = path.resolve(SITE, '..');

const read = (relative) => fs.readFileSync(path.join(SITE, relative), 'utf8');
const html = read('index.html');
const data = JSON.parse(read('data/problems.json'));

const SITE_URL = 'https://problem-solving-daily.vercel.app';
const BRAND = 'Sayanox Problem Solve Daily';

/** All `<meta>` tags, keyed by name/property. */
function metas() {
  const found = new Map();
  for (const tag of html.matchAll(/<meta\s[^>]*>/g)) {
    const key = tag[0].match(/(?:name|property)="([^"]+)"/)?.[1];
    const content = tag[0].match(/content="([^"]*)"/)?.[1];
    if (key && content !== undefined && !found.has(key)) found.set(key, content);
  }
  return found;
}

/** JPEG dimensions from the frame header — no decoder, no dependency. */
function jpegSize(file) {
  const buffer = fs.readFileSync(file);
  assert.equal(buffer[0], 0xff, 'not a JPEG');
  assert.equal(buffer[1], 0xd8, 'not a JPEG');
  let offset = 2;
  while (offset < buffer.length) {
    if (buffer[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = buffer[offset + 1];
    const length = buffer.readUInt16BE(offset + 2);
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      return { height: buffer.readUInt16BE(offset + 5), width: buffer.readUInt16BE(offset + 7) };
    }
    offset += 2 + length;
  }
  throw new Error('no JPEG frame header');
}

const meta = metas();

test('the title leads with the brand and fits a search snippet', () => {
  const title = html.match(/<title>([^<]+)<\/title>/)?.[1];
  assert.ok(title, 'index.html has no <title>');
  assert.ok(title.startsWith(BRAND), `the title must open with the brand: ${title}`);
  assert.ok(title.length <= 70, `the title is ${title.length} characters; Google cuts around 60`);
});

test('the meta description is a full sentence-sized snippet', () => {
  const description = meta.get('description');
  assert.ok(description, 'no meta description');
  assert.ok(description.startsWith(BRAND), 'the description should name the brand first');
  assert.ok(
    description.length >= 120 && description.length <= 170,
    `the description is ${description.length} characters; aim for 120–160`,
  );
  assert.match(description, new RegExp(`${data.stats.tests} tests`), 'the counts must not drift');
});

test('the Search Console ownership token is present, verbatim', () => {
  // Google compares this string byte for byte. An escaped underscore or a smart
  // quote pasted out of a chat window is the classic way to fail verification
  // while the tag looks correct to a human.
  const token = '3ofxmu-_PcySytRPHqZFlIr4AkF-PH5WCz0iiCFzfa4';
  assert.equal(meta.get('google-site-verification'), token);
  assert.ok(
    html.includes(`<meta name="google-site-verification" content="${token}" />`),
    'the verification meta tag must be a plain, unescaped attribute',
  );
  assert.match(token, /^[A-Za-z0-9_-]{43}$/, 'the token shape Google issue');
});

test('the canonical URL, the Open Graph URL and the sitemap agree', () => {
  const canonical = html.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
  assert.equal(canonical, `${SITE_URL}/`);
  assert.equal(meta.get('og:url'), canonical);

  const sitemap = read('sitemap.xml');
  assert.match(sitemap, /^<\?xml version="1\.0" encoding="UTF-8"\?>/);
  assert.match(sitemap, /<urlset xmlns="http:\/\/www\.sitemaps\.org\/schemas\/sitemap\/0\.9">/);
  const locations = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]);
  assert.deepEqual(locations, [canonical], 'the sitemap should list exactly the canonical URL');
  assert.doesNotMatch(sitemap, /<lastmod>/, 'a stale lastmod is worse than none: this page has none');
});

test('robots.txt allows crawling and points at the sitemap', () => {
  const robots = read('robots.txt');
  assert.match(robots, /^User-agent: \*$/m);
  assert.match(robots, /^Allow: \/$/m);
  assert.match(robots, new RegExp(`^Sitemap: ${SITE_URL}/sitemap\\.xml$`, 'm'));
  assert.doesNotMatch(robots, /^Disallow: \/$/m, 'the whole point is to be found');
});

test('every preview image is absolute-able, the right size, and on disk', () => {
  for (const key of ['og:image', 'twitter:image']) {
    assert.equal(
      meta.get(key),
      `${SITE_URL}/assets/og-cover.jpg`,
      `${key} must be absolute: link previews are fetched by crawlers, not browsers`,
    );
  }
  assert.equal(meta.get('og:image:type'), 'image/jpeg');
  assert.equal(meta.get('og:image:width'), '1200');
  assert.equal(meta.get('og:image:height'), '630');

  const cover = path.join(SITE, 'assets/og-cover.jpg');
  const size = jpegSize(cover);
  assert.deepEqual(size, { width: 1200, height: 630 }, 'the image must match its declared size');
  assert.ok(
    fs.statSync(cover).size < 300 * 1024,
    'a preview image over 300 KB is slow to fetch on a phone',
  );
  assert.equal(meta.get('twitter:card'), 'summary_large_image');
});

test('the icons and any other local asset reference exist', () => {
  const referenced = [
    ...[...html.matchAll(/(?:href|content)="(assets\/[^"]+)"/g)].map((match) => match[1]),
  ];
  assert.ok(referenced.length >= 3, 'expected icons and the cover to be referenced');
  for (const relative of referenced) {
    assert.ok(fs.existsSync(path.join(SITE, relative)), `index.html references missing ${relative}`);
  }
  const icon = read('assets/icon.svg');
  assert.match(icon, /^<svg [^>]*viewBox="0 0 64 64"/);
  assert.match(icon, new RegExp(`<title>${BRAND}</title>`));
  assert.match(html, /<link rel="icon" href="assets\/icon\.svg" type="image\/svg\+xml" \/>/);
});

test('the structured data parses and describes the catalogue that exists', () => {
  const blocks = [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)];
  assert.equal(blocks.length, 1, 'exactly one JSON-LD block keeps the graph unambiguous');

  const graph = JSON.parse(blocks[0][1]);
  assert.equal(graph['@context'], 'https://schema.org');

  const byType = (type) => graph['@graph'].find((node) => node['@type'] === type);
  for (const type of ['WebSite', 'Organization', 'CollectionPage', 'ItemList']) {
    assert.ok(byType(type), `the graph is missing a ${type} node`);
  }

  const website = byType('WebSite');
  assert.equal(website.name, BRAND);
  assert.equal(website.url, `${SITE_URL}/`);
  assert.equal(
    website.potentialAction.target.urlTemplate,
    `${SITE_URL}/?q={search_term_string}`,
    'the SearchAction must match the query parameter main.js reads',
  );

  assert.match(byType('Organization').url, /^https:\/\/github\.com\/sayanoxprivatelimited$/);

  const list = byType('ItemList');
  assert.equal(list.numberOfItems, data.stats.problems);
  assert.equal(list.itemListElement.length, data.stats.problems);

  const slugs = data.problems.map((problem) => problem.slug).sort();
  const linked = list.itemListElement.map((entry) => entry.url.replace(`${SITE_URL}/?p=`, '')).sort();
  assert.deepEqual(linked, slugs, 'every problem needs a deep link, and only real ones');

  for (const entry of list.itemListElement) {
    const problem = data.problems.find((candidate) => entry.url.endsWith(candidate.slug));
    assert.ok(problem, `unknown problem in the ItemList: ${entry.url}`);
    assert.equal(entry.position, problem.index, 'positions must follow the catalogue order');
    assert.equal(entry.item.name, problem.title);
    assert.deepEqual(entry.item.teaches, problem.concepts);
    assert.equal(entry.item.educationalLevel, problem.level);
    assert.equal(entry.item.isAccessibleForFree, true);
    assert.ok(entry.item.description.length > 40, `${problem.slug} needs a description for search`);
    assert.ok(entry.item.description.length <= 200, `${problem.slug} description is too long`);
    assert.equal(entry.item.sameAs, problem.brief_url);
  }
});

test('the generated SEO files are up to date', () => {
  // Same contract as `build_catalog.py --check`, enforced from the test suite so a
  // commit cannot land with a stale sitemap or a stale JSON-LD block.
  execFileSync('python3', ['scripts/build_seo.py', '--check'], { cwd: ROOT, stdio: 'pipe' });
});

test('the brand is spelled the same way in the page, the data and the docs', () => {
  const brandPlaces = {
    'navigation wordmark': /<span class="brand__text">Sayanox <span class="brand__name">Problem Solve Daily<\/span><\/span>/,
    'hero headline': /<h1 id="hero-title">\s*Sayanox\s*<span class="gradient">Problem Solve Daily<\/span>\s*<\/h1>/,
    'footer': /<strong>Sayanox Problem Solve Daily<\/strong>/,
  };
  for (const [where, pattern] of Object.entries(brandPlaces)) {
    assert.match(html, pattern, `${where} must carry the brand`);
  }
  assert.match(read('robots.txt'), new RegExp(`^# ${BRAND}`, 'm'));

  const readme = fs.readFileSync(path.join(ROOT, 'README.md'), 'utf8');
  assert.match(readme, /Sayanox Problem Solve Daily/, 'the README should introduce the brand');
});

test('the share targets exist and the page wires them up', () => {
  for (const network of ['x', 'linkedin', 'whatsapp', 'telegram', 'reddit']) {
    assert.match(html, new RegExp(`data-share-net="${network}"`), `missing the ${network} target`);
  }
  for (const id of ['share', 'share-links', 'share-note', 'copy-link', 'hero-share', 'detail-share', 'detail-copy']) {
    assert.match(html, new RegExp(`id="${id}"`), `index.html is missing #${id}`);
  }
  // Every target must work without JavaScript: absolute, encoded, and pointing home.
  for (const tag of html.matchAll(/<a class="share__link" data-share-net="[^"]+"\s+href="([^"]+)"/g)) {
    assert.match(tag[1], /^https:\/\//, 'share links must be absolute');
    assert.match(
      decodeURIComponent(tag[1]),
      new RegExp(SITE_URL.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
      'share links must carry the live URL',
    );
  }

  const main = read('js/main.js');
  assert.match(main, /function shareTargets/);
  assert.match(main, /function initShare/);
  assert.match(main, /navigator\.clipboard\?\.writeText/, 'copy must prefer the async clipboard');
  assert.match(main, /navigator\.share/, 'a phone should get the native share sheet');
});

test('deep links are readable and writable, and match the structured data', () => {
  const main = read('js/main.js');
  for (const fn of ['urlFor', 'replaceUrl', 'applyDeepLink', 'syncUrl']) {
    assert.match(main, new RegExp(`function ${fn}\\(`), `main.js is missing ${fn}()`);
  }
  // replaceState, never pushState: one history entry per keystroke is hostile.
  assert.match(main, /history\.replaceState/);
  assert.doesNotMatch(main, /history\.pushState/);

  // The four shareable parameters are parsed in one place, so a link written by
  // one part of the page cannot be read differently by another.
  const urlState = read('js/urlstate.js');
  assert.match(urlState, /\.get\('q'\)/, 'the search box must read ?q=');
  assert.match(urlState, /\.get\('p'\)/, 'the sheet must read ?p=');
  assert.match(urlState, /\.get\('cat'\)/, 'the category filter must read ?cat=');
  assert.match(urlState, /\.get\('unsolved'\)/, 'the unsolved toggle must read ?unsolved=');
  assert.match(main, /from '\.\/urlstate\.js'/, 'main.js must use the shared parser');
  assert.match(main, /addEventListener\('popstate'/, 'back and forward must restore the view');
  assert.match(main, /#problem-/);
  assert.match(main, /id="problem-\$\{problem\.slug\}"/, 'cards need a stable anchor id');

  // The anchor ids the page advertises in its deep links must not collide with
  // the static ids declared in the markup.
  const staticIds = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]));
  assert.equal(staticIds.size, [...html.matchAll(/\sid="([^"]+)"/g)].length, 'duplicate id in index.html');
});

test('the Python and JavaScript category labels agree', () => {
  const python = fs.readFileSync(path.join(ROOT, 'scripts/build_seo.py'), 'utf8');
  const block = python.match(/CATEGORY_LABELS = \{([\s\S]*?)\n\}/)?.[1];
  assert.ok(block, 'build_seo.py must declare CATEGORY_LABELS');
  const fromPython = Object.fromEntries(
    [...block.matchAll(/"([a-z]+)": "([^"]+)"/g)].map((match) => [match[1], match[2]]),
  );
  const fromJs = Object.fromEntries(
    [...read('js/layout.js').match(/CATEGORY_LABELS = \{([\s\S]*?)\};/)[1].matchAll(
      /([a-z]+): '([^']+)'/g,
    )].map((match) => [match[1], match[2]]),
  );
  assert.deepEqual(fromPython, fromJs, 'structured data and the UI must name categories the same way');
});

test('the deployed shell never ships a relative asset or preview URL', () => {
  const build = fs.readFileSync(path.join(ROOT, 'scripts/build_site_deploy.py'), 'utf8');
  for (const pattern of ['assets/icon.svg', 'assets/apple-touch-icon.png']) {
    assert.ok(build.includes(pattern), `build_site_deploy.py must rewrite ${pattern} to the CDN`);
  }
  assert.match(build, /content="assets\//, 'the remote build must reject a relative preview image');
});
