import { describe, expect, it } from 'vitest';

// MSHL-445 strings (Remote and sharing need Community) follow the short-copy rule of the
// MSHL-448/449/450 UI copy diet: one or two short sentences, no repeated explanation of
// what the service is, in both languages.
type Tree = Record<string, unknown>;

const KEYS = [
  'settings.share.communityOff',
  'settings.remote.badgeHelp.needs_community',
  'settings.remote.badgeHelp.community_off',
  'settings.community.optOut.intro',
  'errors.remote_community_off',
  'errors.share_community_off',
];

const MAX_CHARS = 120;

function flatten(language: 'en' | 'fr'): Map<string, unknown> {
  const slices = import.meta.glob<{ default: Tree }>('../i18n/locales/slices/*.json', {
    eager: true,
  });
  const out = new Map<string, unknown>();
  const collect = (tree: Tree, prefix = '') => {
    for (const [key, value] of Object.entries(tree)) {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        collect(value as Tree, `${prefix}${key}.`);
      } else {
        out.set(`${prefix}${key}`, value);
      }
    }
  };
  for (const [path, mod] of Object.entries(slices)) {
    if (path.endsWith(`.${language}.json`)) collect(mod.default);
  }
  return out;
}

describe('Community-required copy stays short (MSHL-445)', () => {
  for (const language of ['en', 'fr'] as const) {
    const strings = flatten(language);
    for (const key of KEYS) {
      it(`${language} ${key}`, () => {
        const value = strings.get(key);
        expect(typeof value).toBe('string');
        const text = value as string;
        expect(text.length).toBeLessThanOrEqual(MAX_CHARS);
        const sentences = text.split(/[.!?](?:\s|$)/).filter((s) => s.trim() !== '');
        expect(sentences.length).toBeLessThanOrEqual(2);
      });
    }
  }
});
