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

/**
 * The pre-release page. The site builds its docs from the latest stable tag, so that
 * page is not there during a pre-release: get.meshloom.app/next/docs/<lang> redirects
 * to the copy in the latest release candidate, or to the site once there is none.
 */
export function prereleaseDocsUrl(language: string | undefined): string {
  const french = (language ?? '').toLowerCase().startsWith('fr');
  return `https://get.meshloom.app/next/docs/${french ? 'fr' : 'en'}`;
}
