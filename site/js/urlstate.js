/**
 * The URL is the state store: one function to read it, one to write it.
 *
 * The page has four pieces of view state worth sharing — the search box, the open
 * problem sheet, the category filter, and "only what I have not solved". Before this
 * module only the first two made it into the address bar, so "look at the network
 * problems" was a sentence you had to type into chat instead of a link.
 *
 * Rules, in one place so they cannot drift apart:
 *
 *   - `q`       the search box, trimmed; omitted when empty
 *   - `p`       the open problem, by slug; omitted when no sheet is open
 *   - `cat`     the category filter; omitted when `all`
 *   - `unsolved` `1` when only unsolved problems are shown; omitted otherwise
 *
 * Defaults are never written, so a plain visit stays a plain URL — which matters
 * because that is the URL the canonical link and the sitemap advertise.
 */

/** The canonical order of the query string. Stable order = stable diffs and shares. */
export const PARAM_ORDER = ['q', 'p', 'cat', 'unsolved'];

/** A category name is lower-case, digits, and dashes — the catalogue's own shape. */
const CATEGORY_RE = /^[a-z0-9-]+$/;

/**
 * Read the four pieces of state out of a query string.
 *
 * Unknown or malformed values fall back to the default instead of throwing: a link
 * from an older version of the page must still open the page.
 *
 * @param {string} search e.g. `?q=token&cat=data&unsolved=1` (a leading `?` is optional)
 * @returns {{query: string, slug: string, category: string, unsolvedOnly: boolean}}
 */
export function readUrlState(search = '') {
  const params = new URLSearchParams(String(search).replace(/^\?/, ''));

  const query = (params.get('q') ?? '').trim();
  const slug = (params.get('p') ?? '').trim();
  const rawCategory = (params.get('cat') ?? '').trim().toLowerCase();
  const category = CATEGORY_RE.test(rawCategory) ? rawCategory : 'all';

  return {
    query,
    slug,
    category,
    unsolvedOnly: params.get('unsolved') === '1',
  };
}

/**
 * Build the URL for a state, from a base URL that may already carry query or hash.
 *
 * Only non-default values are written, in {@link PARAM_ORDER}, and the hash is dropped:
 * the hash belongs to the card anchors, not to the application state.
 *
 * @param {string|URL} base the current location (or any absolute URL)
 * @param {{query?: string, slug?: string, category?: string, unsolvedOnly?: boolean}} state
 * @returns {string} an absolute URL
 */
export function writeUrlState(base, state = {}) {
  const url = new URL(String(base));
  url.hash = '';
  url.search = '';

  const query = (state.query ?? '').trim();
  if (query) url.searchParams.set('q', query);
  if (state.slug) url.searchParams.set('p', String(state.slug));
  if (state.category && state.category !== 'all') url.searchParams.set('cat', String(state.category));
  if (state.unsolvedOnly) url.searchParams.set('unsolved', '1');

  return url.toString();
}

/**
 * A comparable key for a state, so "did the address bar actually change?" is one
 * string comparison rather than four.
 *
 * @param {{query?: string, slug?: string, category?: string, unsolvedOnly?: boolean}} state
 * @returns {string}
 */
export function stateKey(state = {}) {
  const normalized = {
    query: (state.query ?? '').trim(),
    slug: state.slug ?? '',
    category: state.category ?? 'all',
    unsolvedOnly: Boolean(state.unsolvedOnly),
  };
  return PARAM_ORDER.map((name) => `${name}=${normalized[FIELD_OF[name]]}`).join('&');
}

/** The URL's spelling of a field is not the page's: `p` is `slug`, `unsolved` is a flag. */
const FIELD_OF = { q: 'query', p: 'slug', cat: 'category', unsolved: 'unsolvedOnly' };

/**
 * True when a state shows exactly what a first visit shows — used to decide whether the
 * address bar should carry anything at all.
 *
 * @param {{query?: string, slug?: string, category?: string, unsolvedOnly?: boolean}} state
 * @returns {boolean}
 */
export function isDefaultState(state = {}) {
  const query = (state.query ?? '').trim();
  return !query && !state.slug && (state.category ?? 'all') === 'all' && !state.unsolvedOnly;
}
