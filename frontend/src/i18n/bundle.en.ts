// Everything for one language, built into one file (see the loader in ./index.ts). Keep the
// slices out of any lazy import: each would become its own request at startup (MSHL-470).
import base from './locales/en.json';

export { base };
export const slices = import.meta.glob<Record<string, unknown>>('./locales/slices/*.en.json', {
  eager: true,
  import: 'default',
});
