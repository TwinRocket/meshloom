import {
  parseGif,
  parseLocation,
  parseMeshCoreOneReaction,
  parseReaction,
  splitReplyMention,
} from './meshcoreOpenPayloads';

/**
 * The feed's quick text filter: a case-insensitive substring over what a reader sees as text
 * (the conversation name, the sender and the body) of the rows already loaded. NFKC folds
 * compatibility forms (full-width letters, ligatures) and `toLocaleLowerCase` the case, on
 * both sides. No server round-trip: whole-history search is `SearchView`.
 */

export function normalizeFeedText(value: string): string {
  return value.normalize('NFKC').toLocaleLowerCase();
}

/**
 * True when the body is a structured payload (a MeshCore Open or One reaction `r:...`, a GIF
 * `g:...`, a location `m:...`): its prefix and hash are not text anyone typed, so a query such
 * as `r:` must not match every reaction.
 */
export function isStructuredBody(content: string): boolean {
  const body = splitReplyMention(content)?.body ?? content;
  return (
    parseReaction(body) !== null ||
    parseMeshCoreOneReaction(body) !== null ||
    parseGif(body) !== null ||
    parseLocation(body) !== null
  );
}

export interface FeedTextFields {
  conversation: string;
  sender: string | null;
  /** The body without a channel message's `Sender: ` prefix (`FeedRowLabels.content`). */
  content: string;
}

/** Whether `row` matches an already normalized, non-empty query. */
export function rowMatchesText(fields: FeedTextFields, normalizedQuery: string): boolean {
  if (normalizeFeedText(fields.conversation).includes(normalizedQuery)) return true;
  if (fields.sender && normalizeFeedText(fields.sender).includes(normalizedQuery)) return true;
  return (
    !isStructuredBody(fields.content) && normalizeFeedText(fields.content).includes(normalizedQuery)
  );
}
