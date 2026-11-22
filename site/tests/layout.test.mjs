/**
 * Node's built-in test runner covers the pure layout maths.
 *
 * Three.js needs a real GPU context, so the scenes themselves are not unit-tested
 * here — but every number they draw comes from `layout.js`, and that is testable.
 *
 *   node --test site/tests/
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CATEGORY_COLORS,
  barLayout,
  categoriesOf,
  categoryColor,
  colorToCss,
  filterProblems,
  formatNumber,
  galaxyPositions,
  groupByCategory,
  labelOffset,
  matchesQuery,
  prettyCategory,
  starField,
  stepIndex,
  summarize,
} from '../js/layout.js';

const problems = [
  { index: 1, category: 'ai', title: 'A', level: 'hard', tests: 21, doctests: 5, solution_lines: 300 },
  { index: 2, category: 'ai', title: 'B', level: 'medium', tests: 30, doctests: 4, solution_lines: 250 },
  { index: 3, category: 'backend', title: 'C', level: 'hard', tests: 18, doctests: 5, solution_lines: 200 },
  { index: 4, category: 'data', title: 'D', level: 'medium', tests: 23, doctests: 4, solution_lines: 260 },
];

test('every category has a colour and a css form', () => {
  for (const category of Object.keys(CATEGORY_COLORS)) {
    assert.match(colorToCss(categoryColor(category)), /^#[0-9a-f]{6}$/);
  }
  assert.equal(colorToCss(0x8b5cf6), '#8b5cf6');
  assert.equal(categoryColor('nope'), 0x94a3b8);
});

test('category labels fall back to a capitalised name', () => {
  assert.equal(prettyCategory('ai'), 'AI');
  assert.equal(prettyCategory('backend'), 'Backend');
  assert.equal(prettyCategory('netops'), 'Netops');
});

test('categories and groups are ordered and complete', () => {
  assert.deepEqual(categoriesOf(problems), ['ai', 'backend', 'data']);
  const groups = groupByCategory(problems);
  assert.equal(groups.get('ai').length, 2);
  assert.equal(groups.get('data')[0].index, 4);
});

test('summary adds up the per-problem numbers', () => {
  const stats = summarize(problems);
  assert.equal(stats.problems, 4);
  assert.equal(stats.tests, 92);
  assert.equal(stats.doctests, 18);
  assert.equal(stats.solutionLines, 1010);
  assert.equal(stats.categories, 3);
  assert.deepEqual(stats.levels, { easy: 0, medium: 2, hard: 2 });
});

test('formatNumber groups thousands', () => {
  assert.equal(formatNumber(2682), '2,682');
  assert.equal(formatNumber(7), '7');
});

test('galaxy positions are deterministic, spread out and category-sectorised', () => {
  const first = galaxyPositions(problems);
  const second = galaxyPositions(problems);
  assert.deepEqual(first, second);
  assert.equal(first.length, problems.length);

  for (const node of first) {
    assert.ok(Number.isFinite(node.x) && Number.isFinite(node.y) && Number.isFinite(node.z));
    const distance = Math.hypot(node.x, node.z);
    assert.ok(distance >= 2.69 && distance <= 5.61, `radius out of range: ${distance}`);
    assert.ok(Math.abs(node.y) <= 1.5);
  }

  // Every category owns one angular sector, so same-category nodes share a sector
  // index and different-category nodes never do.
  const sector = (Math.PI * 2) / categoriesOf(problems).length;
  const sectorOf = (node) => Math.floor(node.angle / sector);
  const [a, b, c] = first;
  assert.equal(sectorOf(a), sectorOf(b), 'ai problems share a sector');
  assert.notEqual(sectorOf(a), sectorOf(c), 'backend sits in its own sector');
});

test('galaxy respects custom radii', () => {
  const tight = galaxyPositions(problems, { innerRadius: 1, radius: 2 });
  for (const node of tight) {
    const distance = Math.hypot(node.x, node.z);
    assert.ok(distance >= 0.99 && distance <= 2.01);
  }
});

test('bars scale with the test count and keep a floor', () => {
  const bars = barLayout([...problems, { index: 5, category: 'cli', title: 'E', level: 'easy', tests: 1 }]);
  const tallest = Math.max(...bars.map((bar) => bar.height));
  assert.ok(Math.abs(tallest - 4.2) < 1e-9);
  const smallest = bars.find((bar) => bar.tests === 1);
  assert.equal(smallest.height, 0.35);
  assert.equal(bars[0].color, CATEGORY_COLORS.ai);
  // Bars stay centred on the origin.
  const centre = bars.reduce((total, bar) => total + bar.x, 0) / bars.length;
  assert.ok(Math.abs(centre) < 1e-9);
});

test('every bar carries the fields the hover tooltip prints', () => {
  for (const bar of barLayout(problems)) {
    assert.equal(typeof bar.title, 'string');
    assert.equal(typeof bar.category, 'string');
    assert.ok(['hard', 'medium', 'easy'].includes(bar.level), `${bar.title} has no level`);
    assert.equal(typeof bar.tests, 'number');
  }
});

test('bars are spaced apart so they never intersect', () => {
  const bars = barLayout(problems);
  for (let index = 1; index < bars.length; index += 1) {
    assert.ok(bars[index].x - bars[index - 1].x >= bars[index].width + 0.4);
  }
});

test('star field is deterministic and inside its radius', () => {
  const first = starField(50, { radius: 10, seed: 3 });
  assert.deepEqual(first, starField(50, { radius: 10, seed: 3 }));
  assert.notDeepEqual(first, starField(50, { radius: 10, seed: 4 }));
  assert.equal(first.length, 50);
  for (const star of first) {
    assert.ok(Math.hypot(star.x, star.y, star.z) <= 10.001);
  }
});

test('stepIndex wraps in both directions', () => {
  assert.equal(stepIndex(3, 0, 1), 1);
  assert.equal(stepIndex(3, 2, 1), 0);
  assert.equal(stepIndex(3, 0, -1), 2);
  assert.equal(stepIndex(3, -1, 1), 0);
  assert.equal(stepIndex(0, 0, 1), -1);
});

test('filterProblems keeps catalogue order', () => {
  assert.deepEqual(filterProblems(problems, 'all').map((p) => p.index), [1, 2, 3, 4]);
  assert.deepEqual(filterProblems(problems, 'ai').map((p) => p.index), [1, 2]);
  assert.deepEqual(filterProblems(problems, 'missing'), []);
  assert.notEqual(filterProblems(problems, 'all'), problems, 'must not alias the input');
});

test('labelOffset lifts hard problems a little higher', () => {
  assert.ok(labelOffset({ level: 'hard' }) > labelOffset({ level: 'medium' }));
  assert.equal(labelOffset({}), 0.62);
});

test('matchesQuery finds a problem by any field it shows', () => {
  const bpe = {
    index: 1,
    slug: '01-bpe-tokenizer',
    category: 'ai',
    level: 'hard',
    title: 'Byte-pair encoding tokenizer from scratch',
    concepts: ['tokenization', 'BPE', 'vocabularies'],
    criteria: ['decode(encode(text)) == text'],
    summary: 'Every tokenizer bug is a silent one.',
  };
  const retrieval = { ...bpe, index: 2, slug: '02-retrieval', title: 'Retrieval step for a docs assistant' };

  assert.equal(matchesQuery(bpe, ''), true, 'an empty query matches everything');
  assert.equal(matchesQuery(bpe, '   '), true);
  assert.equal(matchesQuery(bpe, 'TOKEN'), true, 'case-insensitive');
  assert.equal(matchesQuery(bpe, 'bpe'), true, 'matches a concept');
  assert.equal(matchesQuery(bpe, 'hard'), true, 'matches the level');
  assert.equal(matchesQuery(bpe, 'ai'), true, 'matches the category');
  assert.equal(matchesQuery(bpe, 'silent'), true, 'matches the summary');

  assert.equal(matchesQuery(bpe, 'retrieval'), false);
  assert.equal(matchesQuery(retrieval, 'retrieval'), true);
  assert.equal(matchesQuery(retrieval, 'docs assistant'), true);
  assert.equal(matchesQuery(retrieval, 'docs nonsense'), false);
});

test('matchesQuery requires every token, in any order', () => {
  const problem = {
    title: 'Clean a messy sales export',
    level: 'medium',
    category: 'data',
    concepts: ['CSV parsing', 'Decimal money math'],
    criteria: ['Blank cells reject the row'],
    summary: 'Amounts are typed the way humans type them.',
  };

  assert.equal(matchesQuery(problem, 'csv decimal'), true);
  assert.equal(matchesQuery(problem, 'decimal csv'), true);
  assert.equal(matchesQuery(problem, 'csv nginx'), false);
  assert.equal(matchesQuery(problem, 'humans'), true, 'matches the summary');
  assert.equal(matchesQuery(problem, 'reject'), true, 'matches a rule');
});

test('matchesQuery tolerates a problem without the new fields', () => {
  assert.equal(matchesQuery({ title: 'CIDR planner', category: 'network' }, 'planner'), true);
  assert.equal(matchesQuery({ title: 'CIDR planner', category: 'network' }, 'subnet'), false);
});
