import { describe, expect, it } from 'vitest';

import { userDocsUrl } from '../utils/userDocsUrl';

describe('userDocsUrl', () => {
  it('serves French without a locale prefix, like the links inside docs/user/fr', () => {
    expect(userDocsUrl('fr', 'deep/fanout')).toBe('https://meshloom.app/docs/deep/fanout/');
    expect(userDocsUrl('fr-FR', 'around')).toBe('https://meshloom.app/docs/around/');
  });

  it('serves English under /en and falls back to it for other languages', () => {
    expect(userDocsUrl('en', 'deep/home-assistant')).toBe(
      'https://meshloom.app/en/docs/deep/home-assistant/'
    );
    expect(userDocsUrl('de', 'around')).toBe('https://meshloom.app/en/docs/around/');
    expect(userDocsUrl(undefined, 'around')).toBe('https://meshloom.app/en/docs/around/');
  });
});
