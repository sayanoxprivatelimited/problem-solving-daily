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
      level: problem.level,
      tests: problem.tests || 0,
      doctests: problem.doctests || 0,
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

/**
 * Case-insensitive, all-tokens-must-match search over everything a card shows.
 *
 * Every token has to appear somewhere in the haystack, so "csv decimal" finds the
 * messy-export problem without the query having to be in any particular order.
 */
export function matchesQuery(problem, query) {
  const tokens = String(query ?? '')
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (!tokens.length) return true;

  const haystack = [
    problem.title,
    problem.category,
    problem.level,
    problem.slug,
    problem.summary ?? '',
    ...(problem.concepts ?? []),
    ...(problem.criteria ?? []),
  ]
    .join(' ')
    .toLowerCase();

  return tokens.every((token) => haystack.includes(token));
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

/* ------------------------------------------------------------- new, 20-round -- */

/**
 * A subsequence score for quick-switch search.
 *
 * Every character of the query must appear in `text`, in order. The score
 * rewards matches that are contiguous, matches at the start of a word, and an
 * exact substring — and it is deterministic, so the palette never reorders
 * itself between two identical keystrokes.
 *
 * @returns {number} -1 when the query does not match at all
 */
export function fuzzyScore(text, query) {
  const haystack = String(text ?? '').toLowerCase();
  const needle = String(query ?? '').toLowerCase().trim();
  if (!needle) return 0;
  if (!haystack) return -1;

  let score = 0;
  let from = 0;
  let previous = -2;
  for (const char of needle) {
    const found = haystack.indexOf(char, from);
    if (found === -1) return -1;
    score += 1;
    if (found === previous + 1) score += 2;
    if (found === 0 || /[\s·\-_/]/.test(haystack[found - 1])) score += 3;
    previous = found;
    from = found + 1;
  }
  if (haystack === needle) score += 12;
  else if (haystack.includes(needle)) score += 6;
  // Shorter labels win ties: "Data" beats "Data engineering".
  return score - Math.floor(haystack.length / 40);
}

/**
 * Rank problems for the command palette.
 *
 * A query with several words must match all of them (in any field), which is how
 * "hard csv" finds the messy-export problem without knowing its title.
 */
export function rankProblems(problems, query, limit = Infinity) {
  const needle = String(query ?? '').trim();
  if (!needle) return problems.slice(0, limit).map((problem) => ({ problem, score: 0 }));

  const tokens = needle.toLowerCase().split(/\s+/).filter(Boolean);
  const ranked = [];
  for (const problem of problems) {
    const fields = [
      problem.title,
      prettyCategory(problem.category),
      problem.level,
      problem.slug,
      ...(problem.concepts ?? []),
    ];
    // The title is worth double: a hit there is what the visitor typed.
    let best = fuzzyScore(problem.title, needle);
    if (best >= 0) best *= 2;
    for (const field of fields.slice(1)) best = Math.max(best, fuzzyScore(field, needle));

    if (tokens.length > 1) {
      // Every word must appear *somewhere*, as a substring of a field. A fuzzy
      // subsequence across a joined blob would match far too much ("android"
      // scatters through "pagination over a mutable list").
      const lowered = fields.map((field) => String(field ?? '').toLowerCase());
      const all = tokens.every((token) => lowered.some((field) => field.includes(token)));
      if (!all) continue;
      best = Math.max(best, 0) + tokens.length * 4;
    }
    if (best >= 0) ranked.push({ problem, score: best });
  }

  ranked.sort((a, b) => b.score - a.score || a.problem.index - b.problem.index);
  return ranked.slice(0, limit);
}

/**
 * Rank palette commands (problems, category filters, actions) by label first,
 * then keywords. Ties keep the caller's order, so the palette is stable.
 */
export function rankCommands(commands, query, limit = Infinity) {
  const needle = String(query ?? '').trim();
  if (!needle) return commands.slice(0, limit);

  const ranked = [];
  commands.forEach((command, order) => {
    // A label match is what the visitor meant; a keyword match is a good guess.
    const label = fuzzyScore(command.label, needle);
    const keywords = Math.max(...(command.keywords ?? []).map((word) => fuzzyScore(word, needle)), -1);
    const score = Math.max(label >= 0 ? label * 2 : -1, keywords);
    if (score >= 0) ranked.push({ command, score, order });
  });
  ranked.sort((a, b) => b.score - a.score || a.order - b.order);
  return ranked.slice(0, limit).map((entry) => entry.command);
}

/**
 * The "what next?" graph for one problem.
 *
 * A shared concept is the strongest signal, so it is weighted twice as heavily
 * as being in the same category — but a category sibling is still worth showing,
 * because concepts are deliberately specific ("fencing tokens") and most
 * problems share none. Without that fallback the drawer would be empty almost
 * everywhere, which is worse than a slightly weaker recommendation.
 */
export function relatedProblems(problems, slug, limit = 3) {
  const subject = problems.find((problem) => problem.slug === slug);
  if (!subject) return [];
  const mine = new Set((subject.concepts ?? []).map((concept) => concept.toLowerCase()));

  return problems
    .filter((problem) => problem.slug !== slug)
    .map((problem) => {
      const shared = (problem.concepts ?? []).filter((concept) => mine.has(concept.toLowerCase()));
      const sameCategory = problem.category === subject.category;
      return {
        problem,
        shared,
        sameCategory,
        score: shared.length * 2 + (sameCategory ? 1 : 0),
      };
    })
    .filter((entry) => entry.score > 0)
    .sort(
      (a, b) =>
        b.score - a.score
        || Math.abs(a.problem.index - subject.index) - Math.abs(b.problem.index - subject.index)
        || a.problem.index - b.problem.index,
    )
    .slice(0, limit);
}

/** How to describe one entry from `relatedProblems`. */
export function relationLabel({ shared, sameCategory }) {
  if (shared?.length) return `shares ${shared.join(', ')}`;
  return sameCategory ? 'same category' : '';
}

/** Toggle one slug in a set of solved slugs, returning a new set. */
export function toggleSolved(solved, slug) {
  const next = new Set(solved);
  if (next.has(slug)) next.delete(slug);
  else next.add(slug);
  return next;
}

/** How much of the catalogue has been marked solved. */
export function progressSummary(problems, solved) {
  const total = problems.length;
  const done = problems.reduce((count, problem) => count + (solved.has(problem.slug) ? 1 : 0), 0);
  return {
    total,
    solved: done,
    remaining: total - done,
    percent: total ? Math.round((done / total) * 100) : 0,
  };
}
