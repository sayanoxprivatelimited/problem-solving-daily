/**
 * Local progress: which problems the visitor has marked solved.
 *
 * Design rule: storage is a cache, never a requirement. A browser in private
 * mode, a full quota, or JSON written by an older version all end with the same
 * behaviour — the page works, the marks are just not remembered.
 */
export const STORAGE_KEY = 'psd-solved';

/**
 * @param {Storage} [storage] defaults to `localStorage`
 * @returns {Set<string>} the slugs that were stored, or an empty set
 */
export function loadSolved(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((slug) => typeof slug === 'string'));
  } catch {
    return new Set();
  }
}

/**
 * @param {Set<string>} solved
 * @param {Storage} [storage]
 * @returns {boolean} whether the write actually happened
 */
export function saveSolved(solved, storage = globalThis.localStorage) {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify([...solved].sort()));
    return true;
  } catch {
    return false;
  }
}
