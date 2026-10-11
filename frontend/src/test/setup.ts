import '@testing-library/jest-dom/vitest';
import { configure } from '@testing-library/react';
import { afterEach } from 'vitest';
import i18n, { i18nReady } from '../i18n';

await i18nReady;
// Production loads one language at a time; tests switch languages freely.
await i18n.loadLanguages(['en', 'fr']);

/**
 * How long an async assertion waits before calling it a failure.
 *
 * The default is one second, which is a statement about the machine rather than
 * about the app. Measured here: the App-level tests resolve their first assertion
 * in 48ms on an idle machine and 250ms while the suite runs 110 jsdom environments
 * side by side — the work is 20x inside the budget, and what occasionally blew
 * through it was a scheduling spike, not the code under test. Failures stayed real
 * failures; they just were not the ones being reported.
 *
 * Sized on those numbers, and kept well under the per-test timeout so a genuinely
 * unmet expectation still reports as the assertion it is rather than as a test that
 * ran out of time.
 */
configure({ asyncUtilTimeout: 5000 });

// i18n is initialized here (default locale `fr`, fallback `en`).
// Query extracted copy via `i18n.t('key')` so tests stay locale-independent.
// Surfaces left untranslated remain English.

class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserver;

// Several components call matchMedia at import time for responsive detection.
// Use a configurable descriptor so individual tests can override the stub.
if (typeof globalThis.matchMedia === 'undefined') {
  Object.defineProperty(globalThis, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// The Community store is module state: start every test from "unknown". Imported
// lazily so a test file's vi.mock('../api') still applies to the store.
afterEach(async () => {
  const { resetCommunityStoreForTests } = await import('../stores/communityStore');
  resetCommunityStoreForTests();
});
