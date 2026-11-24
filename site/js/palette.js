/**
 * The command palette: one keystroke to anywhere.
 *
 * Design rule: the palette owns *only* the palette. It builds a list of commands,
 * ranks it with a pure function, and hands a command to the caller's handler.
 * Nothing in here touches the catalogue, the scenes or the URL, so it can be
 * tested (and reasoned about) on its own.
 */
import { prettyCategory, rankCommands } from './layout.js';

const LIMIT = 9;

/**
 * @param {object} options
 * @param {Array<object>} options.problems  the catalogue, in index order
 * @param {Array<{id: string, label: string, count: number}>} options.categories
 * @param {(command: object) => void} options.onRun
 * @param {object} options.actions  `{ openProblem, filterCategory, toggleTheme,
 *   copyLink, toggleUnsolved, openRepo, clearFilters }` — all optional
 */
export function buildCommands({ problems, categories, actions = {} }) {
  const commands = [];

  if (actions.openRepo) {
    commands.push({
      id: 'action:repo',
      kind: 'action',
      label: 'Open the repository',
      hint: 'github.com/sayanoxprivatelimited',
      keywords: ['github', 'source', 'code', 'repo'],
      run: actions.openRepo,
    });
  }
  if (actions.copyLink) {
    commands.push({
      id: 'action:copy',
      kind: 'action',
      label: 'Copy a link to this page',
      hint: 'share',
      keywords: ['share', 'link', 'copy', 'url'],
      run: actions.copyLink,
    });
  }
  if (actions.toggleTheme) {
    commands.push({
      id: 'action:theme',
      kind: 'action',
      label: 'Toggle the theme',
      hint: 'dark / light',
      keywords: ['theme', 'dark', 'light', 'colour', 'color'],
      run: actions.toggleTheme,
    });
  }
  if (actions.toggleUnsolved) {
    commands.push({
      id: 'action:unsolved',
      kind: 'action',
      label: 'Show only unsolved problems',
      hint: 'progress',
      keywords: ['unsolved', 'todo', 'progress', 'filter'],
      run: actions.toggleUnsolved,
    });
  }
  if (actions.clearFilters) {
    commands.push({
      id: 'action:clear',
      kind: 'action',
      label: 'Clear the filters',
      hint: 'all categories',
      keywords: ['clear', 'reset', 'all', 'filter'],
      run: actions.clearFilters,
    });
  }

  for (const category of categories) {
    if (category.id === 'all') continue;
    commands.push({
      id: `category:${category.id}`,
      kind: 'category',
      label: `Filter: ${prettyCategory(category.id)}`,
      hint: `${category.count} problem${category.count === 1 ? '' : 's'}`,
      keywords: [category.id, 'category', 'filter'],
      run: () => actions.filterCategory?.(category.id),
    });
  }

  for (const problem of problems) {
    commands.push({
      id: `problem:${problem.slug}`,
      kind: 'problem',
      label: problem.title,
      hint: `${prettyCategory(problem.category)} · ${problem.level} · ${problem.tests} tests`,
      keywords: [
        problem.slug,
        problem.category,
        problem.level,
        ...(problem.concepts ?? []),
        String(problem.tests),
      ],
      problem,
      run: () => actions.openProblem?.(problem.index),
    });
  }

  return commands;
}

function optionMarkup(command, index, active) {
  const selected = index === active ? 'true' : 'false';
  return `
    <li class="palette__item" role="option" id="palette-option-${index}"
        aria-selected="${selected}" data-command="${index}"
        data-kind="${command.kind}">
      <span class="palette__label">${escapeHtml(command.label)}</span>
      <span class="palette__hint">${escapeHtml(command.hint ?? '')}</span>
    </li>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char]);
}

/**
 * Wire the palette to the DOM. Returns a small API so the rest of the page can
 * open it too (a button, a shortcut, a link).
 */
export function initPalette({ problems, categories, actions = {}, document: doc = document }) {
  const root = doc.getElementById('palette');
  const input = doc.getElementById('palette-input');
  const list = doc.getElementById('palette-list');
  const empty = doc.getElementById('palette-empty');
  if (!root || !input || !list) return { open: () => {}, close: () => {}, isOpen: () => false };

  const commands = buildCommands({ problems, categories, actions });
  let matches = commands.slice(0, LIMIT);
  let active = 0;

  function render() {
    list.innerHTML = matches.map((command, index) => optionMarkup(command, index, active)).join('');
    if (empty) empty.hidden = matches.length > 0;
    const current = list.querySelector('[aria-selected="true"]');
    if (current && typeof current.scrollIntoView === 'function') {
      current.scrollIntoView({ block: 'nearest' });
    }
    input.setAttribute('aria-activedescendant', matches.length ? `palette-option-${active}` : '');
  }

  function search(query) {
    matches = rankCommands(commands, query, LIMIT);
    active = 0;
    render();
  }

  function run(index = active) {
    const command = matches[index];
    if (!command) return;
    close();
    command.run();
  }

  function open(initial = '') {
    if (typeof root.showModal === 'function' && !root.open) root.showModal();
    else root.setAttribute('open', '');
    input.value = initial;
    search(initial);
    input.focus();
  }

  function close() {
    if (typeof root.close === 'function' && root.open) root.close();
    else root.removeAttribute('open');
    input.value = '';
  }

  input.addEventListener('input', () => search(input.value));

  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || (event.key === 'n' && event.ctrlKey)) {
      event.preventDefault();
      if (matches.length) {
        active = (active + 1) % matches.length;
        render();
      }
    } else if (event.key === 'ArrowUp' || (event.key === 'p' && event.ctrlKey)) {
      event.preventDefault();
      if (matches.length) {
        active = (active - 1 + matches.length) % matches.length;
        render();
      }
    } else if (event.key === 'Home') {
      event.preventDefault();
      active = 0;
      render();
    } else if (event.key === 'End') {
      event.preventDefault();
      active = matches.length - 1;
      render();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      run();
    }
  });

  // Pointer support: hover moves the active row, click runs it.
  list.addEventListener('pointermove', (event) => {
    const item = event.target instanceof Element ? event.target.closest('[data-command]') : null;
    if (!item) return;
    const index = Number(item.dataset.command);
    if (index !== active && Number.isInteger(index)) {
      active = index;
      render();
    }
  });
  list.addEventListener('click', (event) => {
    const item = event.target instanceof Element ? event.target.closest('[data-command]') : null;
    if (item) run(Number(item.dataset.command));
  });

  // A click on the backdrop closes, like the detail sheet.
  root.addEventListener('click', (event) => {
    if (event.target === root) close();
  });

  return { open, close, isOpen: () => root.hasAttribute('open') || root.open === true, state: () => ({ matches, active }) };
}
