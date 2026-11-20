/**
 * Pure layout maths for the 3D catalogue.
 *
 * Nothing in here touches the DOM or Three.js, so `site/tests/layout.test.mjs`
 * can verify it with Node's built-in test runner. The scenes only turn these
 * numbers into meshes — which means a broken layout is caught by a test instead
 * of by squinting at a canvas.
 */

/** Category -> hex colour. Everything visual derives from this map. */
export const CATEGORY_COLORS = {
  ai: 0x8b5cf6,
  backend: 0x22d3ee,
  cli: 0xf59e0b,
  coding: 0x34d399,
  data: 0x60a5fa,
  network: 0xf472b6,
};

export const FALLBACK_COLOR = 0x94a3b8;

export const CATEGORY_LABELS = {
  ai: 'AI',
  backend: 'Backend',
  cli: 'CLI',
  coding: 'Coding',
  data: 'Data',
  network: 'Network',
};

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

export function categoryColor(category) {
  return CATEGORY_COLORS[category] ?? FALLBACK_COLOR;
}

/** 0x8b5cf6 -> "#8b5cf6" */
export function colorToCss(hex) {
  return `#${(hex >>> 0).toString(16).padStart(6, '0')}`;
}

export function prettyCategory(category) {
  return CATEGORY_LABELS[category] ?? String(category).replace(/^\w/, (c) => c.toUpperCase());
}

/** Ordered, de-duplicated category names. */
export function categoriesOf(problems) {
  return [...new Set(problems.map((problem) => problem.category))].sort();
}

export function groupByCategory(problems) {
  const groups = new Map();
  for (const problem of problems) {
    if (!groups.has(problem.category)) groups.set(problem.category, []);
    groups.get(problem.category).push(problem);
  }
  return groups;
}

/** Totals shown in the hero and the numbers section. */
export function summarize(problems) {
  const levels = { easy: 0, medium: 0, hard: 0 };
  for (const problem of problems) {
    const level = String(problem.level || '').toLowerCase();
    if (level in levels) levels[level] += 1;
  }
  return {
    problems: problems.length,
    tests: problems.reduce((total, problem) => total + (problem.tests || 0), 0),
    doctests: problems.reduce((total, problem) => total + (problem.doctests || 0), 0),
    solutionLines: problems.reduce((total, problem) => total + (problem.solution_lines || 0), 0),
    categories: categoriesOf(problems).length,
    levels,
  };
}

export function formatNumber(value) {
  return new Intl.NumberFormat('en-US').format(value);
}

/**
 * Spiral placement for the catalogue nodes.
 *
 * Categories own angular sectors (so a legend reading clockwise makes sense) and
 * nodes inside a sector fan outwards with alternating height, which keeps every
 * label readable from the default camera angle.
 */
export function galaxyPositions(problems, { innerRadius = 2.7, radius = 5.6, height = 1.5 } = {}) {
  const categories = categoriesOf(problems);
  const groups = groupByCategory(problems);
  const sector = (Math.PI * 2) / Math.max(categories.length, 1);

  return problems.map((problem) => {
    const categoryIndex = categories.indexOf(problem.category);
    const peers = groups.get(problem.category);
    const positionInGroup = peers.indexOf(problem);
    const spread = peers.length === 1 ? 0.5 : (positionInGroup + 0.5) / peers.length;
    const angle = categoryIndex * sector + sector * (0.18 + 0.64 * spread);
    const distance = innerRadius + (radius - innerRadius) * (0.3 + 0.7 * spread);
    const direction = positionInGroup % 2 === 0 ? 1 : -1;

    return {
      index: problem.index,
      category: problem.category,
      angle,
      radius: distance,
      x: Math.cos(angle) * distance,
      y: direction * height * (0.35 + 0.45 * spread),
      z: Math.sin(angle) * distance,
    };
  });
}

/** Bar-chart geometry: height is proportional to the test count, with a floor. */
export function barLayout(problems, { maxHeight = 4.2, spacing = 1.15, minHeight = 0.35 } = {}) {
  const tallest = problems.reduce((max, problem) => Math.max(max, problem.tests || 0), 1);
  const offset = (problems.length - 1) / 2;

  return problems.map((problem, index) => {
    const ratio = (problem.tests || 0) / tallest;
    return {
      index: problem.index,
      title: problem.title,
      category: problem.category,
      tests: problem.tests || 0,
      x: (index - offset) * spacing,
      z: 0,
      width: 0.62,
      depth: 0.62,
      height: Math.max(ratio * maxHeight, minHeight),
      color: categoryColor(problem.category),
    };
  });
}

/** Deterministic pseudo-random star field (no Math.random, so it cannot flicker). */
export function starField(count, { radius = 26, seed = 7 } = {}) {
  let state = seed >>> 0 || 1;
  const next = () => {
    // xorshift32 — tiny, deterministic, good enough for sparks
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    return state / 0xffffffff;
  };

  const stars = [];
  for (let index = 0; index < count; index += 1) {
    const theta = next() * Math.PI * 2;
    const phi = Math.acos(2 * next() - 1);
    const distance = radius * (0.55 + 0.45 * next());
    stars.push({
      x: distance * Math.sin(phi) * Math.cos(theta),
      y: distance * Math.cos(phi) * 0.6,
      z: distance * Math.sin(phi) * Math.sin(theta),
    });
  }
  return stars;
}

/** Next index for keyboard navigation, wrapping at both ends. */
export function stepIndex(length, current, direction) {
  if (length <= 0) return -1;
  const start = current < 0 || current >= length ? (direction > 0 ? -1 : 0) : current;
  return (start + direction + length) % length;
}

/** Problems matching a filter, keeping the catalogue order. */
export function filterProblems(problems, category) {
  if (!category || category === 'all') return problems.slice();
  return problems.filter((problem) => problem.category === category);
}

/** Label placement helpers so the node labels never overlap the spheres. */
export function labelOffset(problem) {
  const level = String(problem.level || 'medium').toLowerCase();
  return level === 'hard' ? 0.72 : 0.62;
}
