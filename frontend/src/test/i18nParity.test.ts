import { describe, expect, it } from 'vitest';

type Tree = Record<string, unknown>;

async function treeFor(language: 'en' | 'fr'): Promise<Map<string, unknown>> {
  const base = (await import(`../i18n/locales/${language}.json`)) as { default: Tree };
  const slices = import.meta.glob<{ default: Tree }>('../i18n/locales/slices/*.json', {
    eager: true,
  });
  const entries = new Map<string, unknown>();
  const collect = (tree: Tree, prefix = '') => {
    for (const [key, value] of Object.entries(tree)) {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        collect(value as Tree, `${prefix}${key}.`);
      } else {
        entries.set(`${prefix}${key}`, value);
      }
    }
  };
  collect(base.default);
  for (const [path, mod] of Object.entries(slices)) {
    if (path.endsWith(`.${language}.json`)) collect(mod.default);
  }
  return entries;
}

async function keysFor(language: 'en' | 'fr'): Promise<Set<string>> {
  return new Set((await treeFor(language)).keys());
}

describe('i18n locale parity', () => {
  // The runtime loads only the active language and has no fallback language, so a key
  // present in one locale and missing from the other would render as a raw key.
  it('en and fr define the same keys', async () => {
    const [en, fr] = await Promise.all([keysFor('en'), keysFor('fr')]);
    expect([...en].filter((k) => !fr.has(k))).toEqual([]);
    expect([...fr].filter((k) => !en.has(k))).toEqual([]);
  });

  it('has no empty translations', async () => {
    for (const language of ['en', 'fr'] as const) {
      const empty = [...(await treeFor(language))]
        .filter(([, value]) => typeof value === 'string' && value.trim() === '')
        .map(([key]) => key);
      expect(empty, language).toEqual([]);
    }
  });

  it('defines every statically referenced key in both locales', async () => {
    const sources = import.meta.glob<string>(
      ['../**/*.{ts,tsx}', '!../test/**', '!../**/*.test.{ts,tsx}'],
      { eager: true, query: '?raw', import: 'default' }
    );
    const used = new Set<string>();
    const callRe = /\b(?:i18n\.)?t\(\s*(['"])([A-Za-z][\w.-]*\.[\w.-]+)\1\s*[,)]/g;
    for (const source of Object.values(sources)) {
      for (const match of source.matchAll(callRe)) used.add(match[2]);
    }
    expect(used.size).toBeGreaterThan(100);
    for (const language of ['en', 'fr'] as const) {
      const known = await keysFor(language);
      const missing = [...used].filter((key) => {
        if (known.has(key)) return false;
        // Plural forms are stored as key_one / key_other.
        return !['_one', '_other', '_zero', '_few', '_many'].some((s) => known.has(key + s));
      });
      expect(missing, language).toEqual([]);
    }
  });
});
