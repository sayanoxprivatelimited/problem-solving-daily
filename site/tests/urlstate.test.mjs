/**
 * The URL is the state store, so these are the tests that keep a share link honest.
 *
 * Everything here is pure: a query string in, a query string out, no DOM. That is the
 * point — the rules for what a URL means live in `urlstate.js`, and the page only calls
 * them, so a link written by an old build still opens something sensible.
 */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  PARAM_ORDER,
  isDefaultState,
  readUrlState,
  stateKey,
  writeUrlState,
} from '../js/urlstate.js';

const BASE = 'https://problem-solving-daily.vercel.app/';

test('an empty query string means the defaults', () => {
  assert.deepEqual(readUrlState(''), {
    query: '',
    slug: '',
    category: 'all',
    unsolvedOnly: false,
  });
});

test('a leading question mark is optional', () => {
  assert.deepEqual(readUrlState('q=token'), readUrlState('?q=token'));
});

test('all four parameters are read', () => {
  assert.deepEqual(readUrlState('?q=token&p=01-bpe&cat=data&unsolved=1'), {
    query: 'token',
    slug: '01-bpe',
    category: 'data',
    unsolvedOnly: true,
  });
});

test('the search term is trimmed, because a trailing space is a typo, not a filter', () => {
  assert.equal(readUrlState('?q=%20%20token%20').query, 'token');
});

test('unsolved is only true for exactly "1"', () => {
  for (const value of ['0', 'true', 'yes', '']) {
    assert.equal(readUrlState(`?unsolved=${value}`).unsolvedOnly, false, value);
  }
  assert.equal(readUrlState('?unsolved=1').unsolvedOnly, true);
});

test('a category name is validated, not trusted', () => {
  assert.equal(readUrlState('?cat=DATA').category, 'data', 'case is normalised');
  assert.equal(readUrlState('?cat=../etc/passwd').category, 'all', 'junk falls back to all');
  assert.equal(readUrlState('?cat=').category, 'all');
});

test('an unknown parameter is ignored rather than preserved', () => {
  assert.deepEqual(readUrlState('?utm_source=news&q=token'), {
    query: 'token',
    slug: '',
    category: 'all',
    unsolvedOnly: false,
  });
});

test('defaults are never written, so a plain visit stays a plain URL', () => {
  assert.equal(writeUrlState(BASE, {}), BASE);
  assert.equal(writeUrlState(BASE, { query: '  ', category: 'all', unsolvedOnly: false }), BASE);
});

test('the parameter order is canonical', () => {
  const url = writeUrlState(BASE, { q: 'x', query: 'token', slug: 's', category: 'data', unsolvedOnly: true });
  assert.equal(new URL(url).search, '?q=token&p=s&cat=data&unsolved=1');
  assert.deepEqual(PARAM_ORDER, ['q', 'p', 'cat', 'unsolved']);
});

test('the hash is dropped: it belongs to the cards, not to the state', () => {
  const url = writeUrlState(`${BASE}#problem-01-bpe`, { query: 'token' });
  assert.equal(new URL(url).hash, '');
});

test('writing replaces whatever query string was there', () => {
  const url = writeUrlState(`${BASE}?utm_source=x&q=old`, { query: 'new' });
  assert.equal(new URL(url).search, '?q=new');
});

test('what is written can be read back, for every combination', () => {
  const states = [
    {},
    { query: 'token' },
    { slug: 'a-b' },
    { category: 'network' },
    { unsolvedOnly: true },
    { query: 'a b & c', slug: 'x/y', category: 'data', unsolvedOnly: true },
  ];
  for (const state of states) {
    const search = new URL(writeUrlState(BASE, state)).search;
    assert.deepEqual(
      readUrlState(search),
      {
        query: state.query ?? '',
        slug: state.slug ?? '',
        category: state.category ?? 'all',
        unsolvedOnly: Boolean(state.unsolvedOnly),
      },
      JSON.stringify(state),
    );
  }
});

test('awkward characters survive the round trip', () => {
  const state = { query: 'a b & c=d', slug: 'কী-বোর্ড' };
  const url = writeUrlState(BASE, state);
  assert.equal(readUrlState(new URL(url).search).query, state.query);
  assert.equal(readUrlState(new URL(url).search).slug, state.slug);
});

test('stateKey ignores a state that only differs by whitespace', () => {
  assert.equal(stateKey({ query: ' token ' }), stateKey({ query: 'token' }));
});

test('stateKey separates every field', () => {
  const keys = new Set([
    stateKey({}),
    stateKey({ query: 'a' }),
    stateKey({ slug: 'a' }),
    stateKey({ category: 'data' }),
    stateKey({ unsolvedOnly: true }),
  ]);
  assert.equal(keys.size, 5);
});

test('stateKey matches what the URL says, so popstate can compare', () => {
  const fromUrl = readUrlState('?q=token&cat=data&unsolved=1');
  assert.equal(
    stateKey(fromUrl),
    stateKey({ query: 'token', category: 'data', unsolvedOnly: true, slug: '' }),
  );
});

test('isDefaultState is true only for a pristine page', () => {
  assert.equal(isDefaultState({}), true);
  assert.equal(isDefaultState({ query: '   ' }), true);
  assert.equal(isDefaultState({ category: 'all' }), true);
  assert.equal(isDefaultState({ query: 'token' }), false);
  assert.equal(isDefaultState({ category: 'network' }), false);
  assert.equal(isDefaultState({ unsolvedOnly: true }), false);
  assert.equal(isDefaultState({ slug: '01-bpe' }), false);
});

test('the module exports nothing that the page does not use', async () => {
  const main = await readFile(new URL('../js/main.js', import.meta.url), 'utf8');
  assert.match(main, /from '\.\/urlstate\.js'/, 'main.js must import the URL rules');
  for (const name of ['readUrlState', 'writeUrlState', 'stateKey', 'isDefaultState']) {
    // A named import that is never mentioned again is dead weight in an entry point.
    const uses = main.match(new RegExp(`\\b${name}\\b`, 'g')) ?? [];
    assert.ok(uses.length >= 2, `${name} is imported but never used`);
  }
});
