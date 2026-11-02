/**
 * Link to a page of the user docs on meshloom.app (`docs/user/<lang>/deep/<page>.md`).
 * The site serves French at the root (`/docs/...`) and English under `/en/docs/...`,
 * the same paths the docs use between themselves (`tests/test_docs_links.py`).
 */
export function userDocUrl(page: string, language: string): string {
  const prefix = language.toLowerCase().startsWith('fr') ? '' : '/en';
  return `https://meshloom.app${prefix}/docs/deep/${page}/`;
}
