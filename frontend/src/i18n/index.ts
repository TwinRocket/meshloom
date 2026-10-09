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
  for (const mod of modules) {
    const overlay =
      mod && typeof mod === 'object' && 'default' in mod
        ? (mod as { default: Record<string, unknown> }).default
        : (mod as Record<string, unknown>);
    if (overlay && typeof overlay === 'object') {
      out = deepMerge(out, overlay);
    }
  }
  return out;
}

type JsonModule = { default: Record<string, unknown> };
type Loader = () => Promise<JsonModule>;

// One entry per language, loaded on demand: only the active language is fetched at
// startup, the other one when the user switches. Slices overlay the base file.
const baseLoaders: Record<string, Loader> = {
  en: () => import('./locales/en.json'),
  fr: () => import('./locales/fr.json'),
};
const sliceLoaders: Record<string, Record<string, Loader>> = {
  en: import.meta.glob<JsonModule>('./locales/slices/*.en.json'),
  fr: import.meta.glob<JsonModule>('./locales/slices/*.fr.json'),
};

async function loadLanguage(language: string): Promise<Record<string, unknown>> {
  const loadBase = baseLoaders[language];
  if (!loadBase) return {};
  const [base, ...slices] = await Promise.all([
    loadBase(),
    ...Object.values(sliceLoaders[language] ?? {}).map((load) => load()),
  ]);
  return mergeSliceModules(base.default, slices);
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
    applyDocumentLanguage(i18n.language);
  });

i18n.on('languageChanged', (language) => {
  applyDocumentLanguage(language);
});

export default i18n;
