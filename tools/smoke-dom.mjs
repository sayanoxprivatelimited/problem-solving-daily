/**
 * DOM smoke test for the interactive features.
 *
 * The unit tests in `site/tests/` cover the pure functions; this covers the
 * wiring — that the palette opens, ranks, and opens a problem, that marking one
 * solved updates the meter and storage, and that the unsolved filter really
 * hides a card. It needs jsdom, so it is not part of `node --test`:
 *
 *   npm install jsdom --no-save
 *   node tools/smoke-dom.mjs
 *
 * Exit code 0 means every check passed.
 */
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const html = readFileSync(new URL('../site/index.html', import.meta.url), 'utf8');
const dom = new JSDOM(html, {
  url: 'https://problem-solving-daily.vercel.app/',
  pretendToBeVisual: true,
  runScripts: 'outside-only',
});

const { window } = dom;
window.matchMedia = (query) => ({
  matches: false,
  media: query,
  addEventListener() {},
  removeEventListener() {},
});
class StubObserver {
  constructor(callback) {
    this.callback = callback;
  }
  observe(element) {
    this.callback([{ target: element, isIntersecting: false }], this);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
window.IntersectionObserver = StubObserver;

globalThis.window = window;
globalThis.document = window.document;
Object.defineProperty(globalThis, 'navigator', { value: window.navigator, configurable: true });
globalThis.location = window.location;
globalThis.HTMLElement = window.HTMLElement;
globalThis.Element = window.Element;
globalThis.Node = window.Node;
globalThis.Event = window.Event;
globalThis.KeyboardEvent = window.KeyboardEvent;
globalThis.CustomEvent = window.CustomEvent;
globalThis.IntersectionObserver = StubObserver;
globalThis.localStorage = window.localStorage;
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

const results = [];
const check = (label, condition, detail = '') => {
  results.push({ label, ok: Boolean(condition), detail });
};

await import('../site/js/main.js');
await new Promise((resolve) => setTimeout(resolve, 120));

const $ = (selector) => window.document.querySelector(selector);
const $$ = (selector) => [...window.document.querySelectorAll(selector)];

const total = JSON.parse(readFileSync(new URL('../site/data/problems.json', import.meta.url), 'utf8'))
  .problems.length;

check('cards render', $$('#cards .card[data-slug]').length === total, `${$$('#cards .card[data-slug]').length}/${total}`);
check('progress starts empty', $('#progress-text')?.textContent === `0 of ${total} solved`, $('#progress-text')?.textContent);
check('palette exists and is closed', $('#palette') && !$('#palette').hasAttribute('open'));

// --- command palette -------------------------------------------------------
$('#palette-trigger').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('palette opens from the button', $('#palette').hasAttribute('open'));

const input = $('#palette-input');
input.value = 'regex';
input.dispatchEvent(new window.Event('input', { bubbles: true }));
const options = $$('#palette-list [data-kind]');
check('palette ranks the regex problem first', options[0]?.textContent.includes('regex'), options[0]?.textContent?.trim());
check('palette shows hints', $$('#palette-list .palette__hint').length > 0);

input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
await new Promise((resolve) => setTimeout(resolve, 60));
check('palette closes after Enter', !$('#palette').hasAttribute('open'));
check('the chosen problem opened in the sheet', $('#detail')?.hasAttribute('open'));
check('sheet title matches', ($('#detail-title')?.textContent ?? '').toLowerCase().includes('regex'), $('#detail-title')?.textContent);
check('related problems are listed', $$('#detail-related li').length > 0, `${$$('#detail-related li').length} entries`);

// --- progress -------------------------------------------------------------
$('#detail-close').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const firstCard = $('#cards .card[data-slug]');
const slug = firstCard.dataset.slug;
firstCard.querySelector('[data-solve]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('progress counts one', $('#progress-text')?.textContent === `1 of ${total} solved`, $('#progress-text')?.textContent);
check('the bar moved', $('#progress-bar')?.style.width !== '0%', $('#progress-bar')?.style.width);
check('the solved slug is stored', JSON.parse(window.localStorage.getItem('psd-solved') ?? '[]').includes(slug));
check('the card is marked', $('#cards .card[data-slug]').classList.contains('is-solved'));

$('#unsolved-toggle').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check(
  'unsolved filter hides the solved card',
  $$('#cards .card[data-slug]').length === total - 1,
  `${$$('#cards .card[data-slug]').length}/${total - 1}`,
);
$('#unsolved-toggle').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('turning the filter off restores the list', $$('#cards .card[data-slug]').length === total);

// --- keyboard shortcut ----------------------------------------------------
window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }));
check('meta+K opens the palette', $('#palette').hasAttribute('open'));

const failures = results.filter((entry) => !entry.ok);
for (const entry of results) {
  console.log(`${entry.ok ? 'ok  ' : 'FAIL'} ${entry.label}${entry.detail ? ` — ${entry.detail}` : ''}`);
}
console.log(`\n${results.length - failures.length}/${results.length} checks passed`);
process.exit(failures.length ? 1 : 0);
