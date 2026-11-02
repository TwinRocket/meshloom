/**
 * Link to a page of the user docs on meshloom.app, in the UI language.
 *
 * Same paths as the links inside docs/user: French is the site's default locale,
 * served without a prefix (`/docs/<page>/`); English lives under `/en/docs/<page>/`.
 * `page` is the path under `docs/user/<lang>/` without `.md` (`deep/fanout`,
 * `around`). Any language other than French gets the English page.
 */
export function userDocsUrl(language: string | undefined, page: string): string {
  const french = (language ?? '').toLowerCase().startsWith('fr');
  return `https://meshloom.app/${french ? '' : 'en/'}docs/${page}/`;
}
