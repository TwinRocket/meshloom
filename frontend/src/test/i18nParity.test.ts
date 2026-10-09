import { describe, expect, it } from 'vitest';

type Tree = Record<string, unknown>;

function flatten(tree: Tree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    value && typeof value === 'object' && !Array.isArray(value)
      ? flatten(value as Tree, `${prefix}${key}.`)
      : [`${prefix}${key}`]
  );
}

async function keysFor(language: 'en' | 'fr'): Promise<Set<string>> {
  const base = (await import(`../i18n/locales/${language}.json`)) as { default: Tree };
  const slices = import.meta.glob<{ default: Tree }>('../i18n/locales/slices/*.json', {
    eager: true,
  });
  const keys = new Set(flatten(base.default));
  for (const [path, mod] of Object.entries(slices)) {
    if (path.endsWith(`.${language}.json`)) flatten(mod.default).forEach((k) => keys.add(k));
  }
  return keys;
}

describe('i18n locale parity', () => {
  // The runtime loads only the active language and has no fallback language, so a key
  // present in one locale and missing from the other would render as a raw key.
  it('en and fr define the same keys', async () => {
    const [en, fr] = await Promise.all([keysFor('en'), keysFor('fr')]);
    expect([...en].filter((k) => !fr.has(k))).toEqual([]);
    expect([...fr].filter((k) => !en.has(k))).toEqual([]);
  });
});
