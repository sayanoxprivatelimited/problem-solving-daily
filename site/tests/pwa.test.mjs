/**
 * Offline support is a handful of files that must agree with each other: the
 * manifest, the service worker's precache list, the page's <head>, and the
 * headers that stop a CDN from serving a stale worker.
 *
 * A service worker that precaches a missing file, or a manifest that points at
 * an icon nobody ships, fails silently in production — so it is checked here.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = join(HERE, '..');

const read = (path) => readFileSync(join(SITE, path), 'utf8');
const manifest = JSON.parse(read('assets/manifest.webmanifest'));
const sw = read('sw.js');
const html = read('index.html');
const main = read('js/main.js');
const vercel = JSON.parse(readFileSync(join(SITE, 'vercel.json'), 'utf8'));

test('the manifest has the fields a browser needs to install the app', () => {
  assert.equal(typeof manifest.name, 'string');
  assert.ok(manifest.name.length > 3);
  assert.equal(typeof manifest.short_name, 'string');
  assert.equal(manifest.start_url, '/');
  assert.equal(manifest.scope, '/');
  assert.equal(manifest.display, 'standalone');
  assert.match(manifest.theme_color, /^#[0-9a-f]{6}$/i);
  assert.match(manifest.background_color, /^#[0-9a-f]{6}$/i);
  assert.deepEqual(manifest.categories, [...manifest.categories].sort(), 'categories stay sorted');
});

test('every manifest icon exists in the bundle', () => {
  assert.ok(manifest.icons.length >= 2);
  for (const icon of manifest.icons) {
    assert.ok(existsSync(join(SITE, 'assets', icon.src)), `missing icon ${icon.src}`);
    assert.ok(icon.sizes);
    assert.ok(icon.type);
  }
  assert.ok(manifest.icons.some((icon) => icon.type === 'image/svg+xml'));
});

test('the page links the manifest and declares a theme colour', () => {
  assert.match(html, /<link rel="manifest" href="assets\/manifest\.webmanifest"/);
  assert.match(html, /<meta name="theme-color" content="#[0-9a-f]{6}"/i);
  assert.match(html, /<meta name="color-scheme"/);
});

test('the service worker precaches only files that exist', () => {
  const listed = [...sw.matchAll(/^\s*'([^']+)',$/gm)].map((match) => match[1]);
  assert.ok(listed.length >= 10, 'the precache list looks empty');
  for (const path of listed) {
    assert.ok(existsSync(join(SITE, path)), `sw.js precaches a missing file: ${path}`);
  }
});

test('the precache list covers the shell that makes an offline load work', () => {
  for (const required of ['index.html', 'offline.html', 'css/style.css', 'js/main.js', 'data/catalog.js']) {
    assert.ok(sw.includes(`'${required}'`), `sw.js does not precache ${required}`);
  }
});

test('the worker handles install, activate and fetch', () => {
  for (const event of ['install', 'activate', 'fetch']) {
    assert.match(sw, new RegExp(`addEventListener\\('${event}'`), `no ${event} handler`);
  }
  assert.match(sw, /caches\.keys\(\)/, 'old caches must be dropped on activate');
  assert.match(sw, /skipWaiting/, 'a new worker should take over without a second visit');
  assert.match(sw, /clients\.claim/);
});

test('navigation is network-first so a deploy is visible immediately', () => {
  assert.match(sw, /request\.mode === 'navigate'/);
  const navigation = sw.slice(sw.indexOf("request.mode === 'navigate'"));
  assert.ok(
    navigation.indexOf('fetch(request)') < navigation.indexOf("cache.match('index.html')"),
    'the network must be tried before the cached page',
  );
});

test('the worker is registered, guarded, and only over a secure origin', () => {
  assert.match(main, /'serviceWorker' in navigator/);
  assert.match(main, /serviceWorker\.register\('sw\.js'\)/);
  assert.match(main, /https:/, 'a worker needs TLS; the guard must say so');
});

test('the offline fallback is noindex, so it cannot be crawled as content', () => {
  const offline = read('offline.html');
  assert.match(offline, /<meta name="robots" content="noindex"/);
  assert.match(offline, /Try again/);
});

test('the CDN is told not to cache the worker, and to allow root scope', () => {
  const swHeaders = vercel.headers.find((entry) => entry.source === '/sw.js');
  assert.ok(swHeaders, 'vercel.json has no /sw.js rule');
  const keys = Object.fromEntries(swHeaders.headers.map((header) => [header.key, header.value]));
  assert.match(keys['Cache-Control'], /max-age=0/);
  assert.equal(keys['Service-Worker-Allowed'], '/');
});

test('the manifest is served with the manifest content type', () => {
  const rule = vercel.headers.find((entry) => entry.source === '/assets/manifest.webmanifest');
  assert.ok(rule, 'vercel.json has no manifest rule');
  const keys = Object.fromEntries(rule.headers.map((header) => [header.key, header.value]));
  assert.equal(keys['Content-Type'], 'application/manifest+json');
});
