/**
 * Page wiring: load the generated catalogue data, render the DOM, boot the scenes.
 *
 * Design rule: the DOM is the source of truth for content (it works without
 * WebGL), and the scenes are an enhancement. Each scene boots inside its own
 * try/catch, so one broken canvas never takes the page down.
 */
import {
  categoryColor,
  colorToCss,
  filterProblems,
  formatNumber,
  prettyCategory,
  summarize,
} from './layout.js';
import { createBarsScene } from './scenes/bars.js';
import { createGalaxyScene } from './scenes/galaxy.js';
import { createHeroScene } from './scenes/hero.js';
import { createStructureScene } from './scenes/structure.js';

const state = {
  problems: [],
  category: 'all',
  selected: null,
  galaxy: null,
};

const tooltip = document.getElementById('tooltip');

/* ------------------------------------------------------------------ data -- */
async function loadData() {
  const response = await fetch('./data/problems.json', { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status} for data/problems.json`);
  return response.json();
}

function showDataError(error) {
  document.body.classList.add('no-webgl');
  const cards = document.getElementById('cards');
  cards.innerHTML = `
    <div class="card">
      <h3 class="card__title">The catalogue data is missing</h3>
      <p class="card__concepts">
        Run <code>python scripts/build_catalog.py</code> to generate
        <code>site/data/problems.json</code>, then serve the folder:
        <code>python3 -m http.server --directory site</code>.
      </p>
      <p class="card__concepts">(${error.message})</p>
    </div>`;
  const footer = document.getElementById('footer-meta');
  if (footer) footer.textContent = 'data unavailable — see the message above';
}

/* ------------------------------------------------------------------- DOM -- */
function hydrateLinks(data) {
  const repo = data.repository?.url ?? '#';
  for (const id of ['repo-link', 'hero-repo']) {
    const element = document.getElementById(id);
    if (element) element.href = repo;
  }
}

function renderHeroStats(data) {
  const stats = summarize(state.problems);
  const items = [
    [formatNumber(stats.problems), 'problems'],
    [formatNumber(stats.tests), 'tests'],
    [formatNumber(stats.doctests), 'doctests'],
    [formatNumber(stats.categories), 'categories'],
  ];

  const container = document.getElementById('hero-stats');
  container.innerHTML = items
    .map(
      ([value, label]) => `
      <div class="stat">
        <span class="stat__value">${value}</span>
        <span class="stat__label">${label}</span>
      </div>`,
    )
    .join('');
}

function renderLegend(categories) {
  const legend = document.getElementById('hero-legend');
  legend.innerHTML = categories
    .map(
      (category) => `
      <li style="--cat: ${colorToCss(categoryColor(category.name))}">
        <span class="legend__swatch" aria-hidden="true"></span>
        ${prettyCategory(category.name)} · ${category.problems}
      </li>`,
    )
    .join('');
}

function renderFilters(categories) {
  const container = document.getElementById('filters');
  const options = [{ name: 'all', label: 'All', count: state.problems.length }].concat(
    categories.map((category) => ({
      name: category.name,
      label: prettyCategory(category.name),
      count: category.problems,
    })),
  );

  container.innerHTML = options
    .map(
      (option) => `
      <button class="filter" type="button" data-category="${option.name}"
        aria-pressed="${option.name === state.category}"
        style="--cat: ${colorToCss(categoryColor(option.name))}">
        ${option.label} <span aria-hidden="true">${option.count}</span>
      </button>`,
    )
    .join('');

  container.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-category]');
    if (!button) return;
    setCategory(button.dataset.category);
  });
}

function renderCards() {
  const container = document.getElementById('cards');
  const visible = filterProblems(state.problems, state.category);

  container.innerHTML = visible
    .map((problem) => {
      const color = colorToCss(categoryColor(problem.category));
      return `
        <article class="card" style="--cat: ${color}" data-index="${problem.index}">
          <header class="card__head">
            <span class="card__index">${String(problem.index).padStart(2, '0')}</span>
            <span class="badge">${prettyCategory(problem.category)}</span>
            <span class="badge badge--level" data-level="${problem.level}">${problem.level}</span>
          </header>
          <h3 class="card__title">
            <a href="${problem.brief_url}" target="_blank" rel="noreferrer">${problem.title}</a>
          </h3>
          <p class="card__concepts">${problem.concepts.join(' · ')}</p>
          <footer class="card__foot">
            <span>${problem.tests} tests</span>
            <span>${problem.doctests} doctests</span>
            <a href="${problem.code_url}" target="_blank" rel="noreferrer">code ↗</a>
          </footer>
        </article>`;
    })
    .join('');

  for (const card of container.querySelectorAll('.card[data-index]')) {
    const index = Number(card.dataset.index);
    card.addEventListener('pointerenter', () => state.galaxy?.focus(index));
    card.addEventListener('pointerleave', () => state.galaxy?.focus(null));
  }

  if (state.selected) paintSelection(state.selected);
}

function renderNumbers(data) {
  const stats = summarize(state.problems);
  const stack = document.getElementById('stat-stack');
  const levelRows = ['hard', 'medium', 'easy']
    .map((level) => {
      const count = stats.levels[level] ?? 0;
      const share = stats.problems ? Math.round((count / stats.problems) * 100) : 0;
      return `
        <div class="level-row">
          <span>${level}</span>
          <span class="level-bar"><span style="width: ${share}%"></span></span>
          <span>${count}</span>
        </div>`;
    })
    .join('');

  stack.innerHTML = `
    <div class="stat-card">
      <div class="stat-card__value">${formatNumber(stats.tests)}</div>
      <div class="stat-card__label">tests, all green</div>
    </div>
    <div class="stat-card">
      <div class="stat-card__value">${formatNumber(stats.doctests)}</div>
      <div class="stat-card__label">doctest examples executed</div>
    </div>
    <div class="stat-card">
      <div class="stat-card__value">${formatNumber(stats.solutionLines)}</div>
      <div class="stat-card__label">lines of solution code</div>
    </div>
    <div class="stat-card">
      <div class="stat-card__value">${stats.categories}</div>
      <div class="stat-card__label">categories</div>
      <div class="levels">${levelRows}</div>
    </div>`;

  const footer = document.getElementById('footer-meta');
  if (footer) {
    footer.textContent = `${stats.problems} problems · ${formatNumber(stats.tests)} tests · generated from problem.md front matter`;
  }
}

/* -------------------------------------------------------------- selection -- */
function paintSelection(index) {
  for (const card of document.querySelectorAll('.card[data-index]')) {
    card.classList.toggle('is-active', Number(card.dataset.index) === index);
  }
}

function selectProblem(problem) {
  state.selected = problem.index;
  paintSelection(problem.index);
  state.galaxy?.focus(problem.index);
  const card = document.querySelector(`.card[data-index="${problem.index}"]`);
  if (card) {
    card.classList.add('is-active');
    card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

function setCategory(category) {
  state.category = category;
  for (const button of document.querySelectorAll('button[data-category]')) {
    button.setAttribute('aria-pressed', String(button.dataset.category === category));
  }
  renderCards();
  state.galaxy?.setFilter(category);
}

/* --------------------------------------------------------------- tooltip -- */
function showTooltip(problem, point) {
  if (!problem || !point) {
    tooltip.hidden = true;
    return;
  }
  tooltip.innerHTML = `<strong>${problem.title}</strong>
    <span>${prettyCategory(problem.category)} · ${problem.level} · ${problem.tests} tests</span>`;
  tooltip.hidden = false;
  tooltip.style.left = `${point.x}px`;
  tooltip.style.top = `${point.y}px`;
}

function hideTooltip() {
  tooltip.hidden = true;
}

function tooltipHandlers() {
  return {
    onHover: (problem, point) => (problem ? showTooltip(problem, point) : hideTooltip()),
  };
}

/* ---------------------------------------------------------------- scenes -- */
function bootScene(name, factory) {
  try {
    return factory();
  } catch (error) {
    console.warn(`[${name}] scene unavailable:`, error);
    document.body.classList.add('no-webgl');
    return null;
  }
}

function startScenes(data) {
  const heroCanvas = document.getElementById('hero-canvas');
  bootScene('hero', () => createHeroScene(heroCanvas, state.problems));

  const galaxyCanvas = document.getElementById('galaxy-canvas');
  state.galaxy = bootScene('galaxy', () =>
    createGalaxyScene(galaxyCanvas, state.problems, {
      ...tooltipHandlers(),
      onSelect: selectProblem,
    }),
  );

  const barsCanvas = document.getElementById('bars-canvas');
  bootScene('bars', () =>
    createBarsScene(barsCanvas, state.problems, tooltipHandlers()),
  );

  const structureCanvas = document.getElementById('structure-canvas');
  bootScene('structure', () =>
    createStructureScene(structureCanvas, {
      labels: ['problem.md', 'solution/', 'tests/'],
    }),
  );

  // Filters also dim the galaxy once it exists.
  state.galaxy?.setFilter(state.category);
}

/* ------------------------------------------------------------------ init -- */
async function bootstrap() {
  try {
    const data = await loadData();
    state.problems = data.problems;

    hydrateLinks(data);
    renderHeroStats(data);
    renderLegend(data.categories);
    renderFilters(data.categories);
    renderCards();
    renderNumbers(data);
    startScenes(data);

    // Escape clears a selection so keyboard users can leave "card focus" mode.
    window.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        state.selected = null;
        paintSelection(null);
        state.galaxy?.focus(null);
        hideTooltip();
      }
    });
  } catch (error) {
    console.error(error);
    showDataError(error);
  }
}

bootstrap();
