import { describe, expect, it, vi } from 'vitest';

describe('i18nReady', () => {
  it('rejects when the active locale chunk cannot be imported', async () => {
    vi.resetModules();
    vi.doMock('../i18n/locales/fr.json', () => {
      throw new Error('Failed to fetch dynamically imported module');
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const mod = await import('../i18n');
    await expect(mod.i18nReady).rejects.toThrow(/failed to load/);
    vi.doUnmock('../i18n/locales/fr.json');
  });
});
