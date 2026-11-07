import { describe, expect, it } from 'vitest';

import i18n, { i18nReady } from '../i18n';

type Tree = Record<string, unknown>;

const slices = import.meta.glob<Tree>('../i18n/locales/slices/*.json', {
  eager: true,
  import: 'default',
});

function leaves(tree: Tree, prefix = '', out = new Map<string, unknown>()) {
  for (const [key, value] of Object.entries(tree)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      leaves(value as Tree, `${prefix}${key}.`, out);
    } else {
      out.set(`${prefix}${key}`, value);
    }
  }
  return out;
}

describe('language bundle', () => {
  // One file per language (MSHL-470) must still hold every slice, later slices winning.
  it.each(['en', 'fr'] as const)('holds every slice of %s, over the base', async (language) => {
    await i18nReady;
    await i18n.loadLanguages(language);
    const bundle = leaves(i18n.getResourceBundle(language, 'translation') as Tree);
    const expected = new Map<string, unknown>();
    for (const [path, slice] of Object.entries(slices).sort(([a], [b]) => (a < b ? -1 : 1))) {
      if (path.endsWith(`.${language}.json`)) leaves(slice, '', expected);
    }
    expect(expected.size).toBeGreaterThan(0);
    const differing = [...expected].filter(([key, value]) => bundle.get(key) !== value);
    expect(differing).toEqual([]);
  });

  it('imports the slices with the bundle, not one by one', async () => {
    for (const load of [() => import('../i18n/bundle.en'), () => import('../i18n/bundle.fr')]) {
      const { base, slices: loaded } = await load();
      expect(Object.keys(base).length).toBeGreaterThan(0);
      for (const slice of Object.values(loaded)) expect(typeof slice).toBe('object');
    }
  });
});
