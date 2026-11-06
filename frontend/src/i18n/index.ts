import i18n, { type BackendModule } from 'i18next';
import { initReactI18next } from 'react-i18next';

import {
  DEFAULT_LOCALE,
  FALLBACK_LOCALE,
  applyDocumentLanguage,
  getSavedLanguage,
} from '../utils/languagePreference';

function deepMerge(
  base: Record<string, unknown>,
  overlay: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(overlay)) {
    const existing = out[key];
    if (
      value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      existing &&
      typeof existing === 'object' &&
      !Array.isArray(existing)
    ) {
      out[key] = deepMerge(existing as Record<string, unknown>, value as Record<string, unknown>);
    } else {
      out[key] = value;
    }
  }
  return out;
}

function mergeSliceModules(
  base: Record<string, unknown>,
  modules: unknown[]
): Record<string, unknown> {
  let out = base;
  for (const overlay of modules) {
    if (overlay && typeof overlay === 'object') {
      out = deepMerge(out, overlay as Record<string, unknown>);
    }
  }
  return out;
}

type Bundle = { base: Record<string, unknown>; slices: Record<string, unknown> };

// One file per language, loaded on demand: only the active language is fetched at startup, the
// other one when the user switches. A file per slice would mean some 90 requests at once, and
// the Remote relay refuses a request beyond 64 open at a time (MSHL-470).
const bundleLoaders: Record<string, () => Promise<Bundle>> = {
  en: () => import('./bundle.en'),
  fr: () => import('./bundle.fr'),
};

async function loadLanguage(language: string): Promise<Record<string, unknown>> {
  const load = bundleLoaders[language];
  if (!load) return {};
  const { base, slices } = await load();
  // Slices overlay the base file, in file name order.
  return mergeSliceModules(base, Object.values(slices));
}

const lazyBackend: BackendModule = {
  type: 'backend',
  init() {},
  read(language, _namespace, callback) {
    loadLanguage(language).then(
      (data) => callback(null, data),
      (err: unknown) => callback(err instanceof Error ? err : new Error(String(err)), null)
    );
  },
};

applyDocumentLanguage(getSavedLanguage());

/** Resolves once the active language is loaded. Await it before first render. */
export const i18nReady: Promise<void> = i18n
  .use(lazyBackend)
  .use(initReactI18next)
  .init({
    lng: getSavedLanguage(),
    // No runtime fallback: en and fr are kept at key parity (see i18nParity.test.ts),
    // so the fallback language never needs to be fetched alongside the active one.
    fallbackLng: false,
    supportedLngs: [DEFAULT_LOCALE, FALLBACK_LOCALE],
    ns: ['translation'],
    defaultNS: 'translation',
    partialBundledLanguages: true,
    interpolation: { escapeValue: false },
  })
  .then(() => {
    // The backend reports a failed chunk import through i18next's log, not by rejecting
    // init(), so check the bundle actually arrived: raw keys are not an acceptable UI.
    if (!i18n.hasResourceBundle(i18n.language, 'translation')) {
      throw new Error(`Locale bundle "${i18n.language}" failed to load`);
    }
    applyDocumentLanguage(i18n.language);
  });

i18n.on('languageChanged', (language) => {
  applyDocumentLanguage(language);
});

export default i18n;
