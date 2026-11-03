import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { userDocsUrl } from '../utils/userDocsUrl';

describe('userDocsUrl', () => {
  it('serves French without a locale prefix, like the links inside docs/user/fr', () => {
    expect(userDocsUrl('fr', 'deep/fanout')).toBe('https://meshloom.app/docs/deep/fanout/');
    expect(userDocsUrl('fr-FR', 'around')).toBe('https://meshloom.app/docs/around/');
    expect(userDocsUrl('fr', 'deep/community')).toBe('https://meshloom.app/docs/deep/community/');
    expect(userDocsUrl('fr', 'deep/share')).toBe('https://meshloom.app/docs/deep/share/');
  });

  it('serves English under /en and falls back to it for other languages', () => {
    expect(userDocsUrl('en', 'deep/home-assistant')).toBe(
      'https://meshloom.app/en/docs/deep/home-assistant/'
    );
    expect(userDocsUrl('en-US', 'deep/remote')).toBe('https://meshloom.app/en/docs/deep/remote/');
    expect(userDocsUrl('de', 'around')).toBe('https://meshloom.app/en/docs/around/');
    expect(userDocsUrl(undefined, 'around')).toBe('https://meshloom.app/en/docs/around/');
  });

  it('never doubles the locale (language and page are not swapped)', () => {
    // userDocsUrl(page, language) would yield https://meshloom.app/en/docs/en/.
    for (const url of [userDocsUrl('en', 'deep/remote'), userDocsUrl('fr', 'deep/share')]) {
      expect(url).not.toMatch(/\/docs\/(en|fr)(-[A-Z]{2})?\//);
    }
  });
});

// One docs-URL helper only: a second one with swapped (string, string) arguments type-checks
// at every call site and silently builds wrong links (MSHL-447).
describe('docs URL helpers', () => {
  function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) return name === 'test' ? [] : sources(path);
      return /\.tsx?$/.test(name) ? [path] : [];
    });
  }

  it('builds every meshloom.app docs link through userDocsUrl', () => {
    const root = join(__dirname, '..');
    const offenders = sources(root).filter((file) => {
      if (file.endsWith(join('utils', 'userDocsUrl.ts'))) return false;
      return /meshloom\.app\/(\$\{[^}]*\}|en\/|fr\/)?docs\//.test(readFileSync(file, 'utf8'));
    });
    expect(offenders).toEqual([]);
  });
});
