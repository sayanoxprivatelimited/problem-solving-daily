/**
 * The four things that make the page feel like a product rather than a page:
 * the command palette's ranking, local progress, the related-problem graph, and
 * the fuzzy score all of them share.
 *
 * All of it is pure — no DOM, no storage, no Three.js — so it is tested here and
 * the wiring in `main.js` stays thin.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import catalog from '../data/catalog.js';
import {
  fuzzyScore,
  progressSummary,
  rankCommands,
  rankProblems,
  relatedProblems,
  relationLabel,
  toggleSolved,
} from '../js/layout.js';

const PROBLEMS = catalog.problems;

/* ------------------------------------------------------------------ fuzzy -- */

test('an empty query scores zero, not a match', () => {
  assert.equal(fuzzyScore('anything', ''), 0);
  assert.equal(fuzzyScore('anything', '   '), 0);
});

test('a query that is not a subsequence does not match', () => {
  assert.equal(fuzzyScore('tokenizer', 'xyz'), -1);
  assert.equal(fuzzyScore('tokenizer', 'zt'), -1, 'out of order must not match');
});

test('matching is case-insensitive', () => {
  assert.ok(fuzzyScore('Tokenizer', 'tok') > 0);
  assert.equal(fuzzyScore('Tokenizer', 'tok'), fuzzyScore('tokenizer', 'tok'));
});

test('an exact match beats a scattered subsequence', () => {
  assert.ok(fuzzyScore('data', 'data') > fuzzyScore('deployment analysis test array', 'data'));
});

test('a contiguous run beats the same characters spread out', () => {
  assert.ok(fuzzyScore('token bucket', 'token') > fuzzyScore('token bucket', 'tbn'));
});

test('a match at a word start beats a match in the middle', () => {
  assert.ok(fuzzyScore('rate limiter', 'lim') > fuzzyScore('sublimate', 'lim'));
});

/* ---------------------------------------------------------- rank problems -- */

test('an empty query keeps catalogue order', () => {
  const ranked = rankProblems(PROBLEMS, '');
  assert.deepEqual(
    ranked.map((entry) => entry.problem.index),
    PROBLEMS.map((problem) => problem.index),
  );
});

test('a title match finds the problem', () => {
  const [first] = rankProblems(PROBLEMS, 'tokenizer');
  assert.equal(first.problem.slug, '01-bpe-tokenizer');
});

test('a concept match finds the problem', () => {
  const ranked = rankProblems(PROBLEMS, 'fencing tokens');
  assert.ok(ranked.some((entry) => entry.problem.slug === '02-lease-based-lock-with-fencing-tokens'));
});

test('two words must both match somewhere', () => {
  const ranked = rankProblems(PROBLEMS, 'android kubernetes');
  assert.equal(ranked.length, 0);
});

test('two words that do match narrow the list', () => {
  const ranked = rankProblems(PROBLEMS, 'hard regex');
  assert.ok(ranked.length >= 1);
  for (const entry of ranked) {
    const haystack = [
      entry.problem.title,
      entry.problem.category,
      entry.problem.level,
      ...(entry.problem.concepts ?? []),
    ].join(' ').toLowerCase();
    assert.ok(haystack.includes('hard'), `${entry.problem.slug} lacks "hard"`);
    assert.ok(haystack.includes('regex'), `${entry.problem.slug} lacks "regex"`);
  }
});

test('ties keep catalogue order and the limit is respected', () => {
  const ranked = rankProblems(PROBLEMS, 'a', 5);
  assert.equal(ranked.length, 5);
  for (let index = 1; index < ranked.length; index += 1) {
    const previous = ranked[index - 1];
    const current = ranked[index];
    if (previous.score === current.score) {
      assert.ok(previous.problem.index < current.problem.index, 'tie-break must be by index');
    }
  }
});

/* --------------------------------------------------------- rank commands -- */

const COMMANDS = [
  { label: 'Toggle the theme', keywords: ['dark', 'light'], id: 'theme' },
  { label: 'Filter: Backend', keywords: ['backend', 'filter'], id: 'backend' },
  { label: 'Byte-pair encoding tokenizer from scratch', keywords: ['tokenization', 'bpe'], id: 'bpe' },
  { label: 'Copy a link to this page', keywords: ['share', 'url'], id: 'copy' },
];

test('commands rank by label first', () => {
  const ranked = rankCommands(COMMANDS, 'toggle');
  assert.equal(ranked[0].id, 'theme');
});

test('commands rank by keyword too', () => {
  const ranked = rankCommands(COMMANDS, 'bpe');
  assert.equal(ranked[0].id, 'bpe');
});

test('an empty query keeps the caller order', () => {
  assert.deepEqual(rankCommands(COMMANDS, '').map((command) => command.id), ['theme', 'backend', 'bpe', 'copy']);
});

test('commands that do not match are dropped, and the limit applies', () => {
  assert.deepEqual(rankCommands(COMMANDS, 'zzzz'), []);
  assert.equal(rankCommands(COMMANDS, 'e', 2).length, 2);
});

/* ---------------------------------------------------------------- related -- */

test('related problems are never the problem itself, and never empty', () => {
  for (const problem of PROBLEMS) {
    const related = relatedProblems(PROBLEMS, problem.slug);
    assert.ok(related.length > 0, `${problem.slug} has no related problems`);
    for (const entry of related) {
      assert.notEqual(entry.problem.slug, problem.slug, 'never relate a problem to itself');
      assert.ok(entry.score > 0);
    }
  }
});

test('a shared concept outranks a category sibling', () => {
  // The regex engine shares "backtracking"-style concepts with nothing, so it
  // falls back to its category — but every scored entry must still explain itself.
  for (const entry of relatedProblems(PROBLEMS, '03-a-regex-engine-with-bounded-backtracking')) {
    assert.ok(entry.shared.length > 0 || entry.sameCategory);
    assert.ok(relationLabel(entry).length > 0);
  }
  const withConcept = relatedProblems(PROBLEMS, '01-bpe-tokenizer').find(
    (entry) => entry.shared.length > 0,
  );
  if (withConcept) {
    assert.match(relationLabel(withConcept), /^shares /);
    assert.ok(withConcept.score >= 2, 'a shared concept is worth at least two points');
  }
});

test('the strongest overlap comes first', () => {
  const related = relatedProblems(PROBLEMS, '01-bpe-tokenizer', 5);
  for (let index = 1; index < related.length; index += 1) {
    assert.ok(related[index - 1].shared.length >= related[index].shared.length);
  }
});

test('an unknown slug relates to nothing', () => {
  assert.deepEqual(relatedProblems(PROBLEMS, 'no-such-problem'), []);
});

test('the limit is respected', () => {
  assert.ok(relatedProblems(PROBLEMS, '01-bpe-tokenizer', 2).length <= 2);
});

/* --------------------------------------------------------------- progress -- */

test('toggling marks a problem solved and unsolved again', () => {
  const empty = new Set();
  const solved = toggleSolved(empty, 'a-slug');
  assert.ok(solved.has('a-slug'));
  assert.equal(empty.size, 0, 'the input set must not be mutated');
  assert.ok(!toggleSolved(solved, 'a-slug').has('a-slug'));
});

test('progress is counted from the catalogue, not from the stored keys', () => {
  const summary = progressSummary(PROBLEMS, new Set(['01-bpe-tokenizer', 'not-a-real-slug']));
  assert.equal(summary.total, PROBLEMS.length);
  assert.equal(summary.solved, 1, 'stale slugs in storage must not inflate the count');
  assert.equal(summary.remaining, PROBLEMS.length - 1);
});

test('progress percentage rounds and never divides by zero', () => {
  const none = progressSummary(PROBLEMS, new Set());
  assert.equal(none.percent, 0);
  const all = progressSummary(PROBLEMS, new Set(PROBLEMS.map((problem) => problem.slug)));
  assert.equal(all.percent, 100);
  assert.equal(all.remaining, 0);
  assert.deepEqual(progressSummary([], new Set()), { total: 0, solved: 0, remaining: 0, percent: 0 });
});
