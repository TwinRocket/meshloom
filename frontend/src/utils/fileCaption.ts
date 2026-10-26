import { byteLength, shortFormBytes } from './share';

/** `📷 ` before the link: the API's suggested text is `📷 <url>` (`share_upload.suggested_text`). */
const PREFIX_BYTES = byteLength('\u{1F4F7} ');

/**
 * The message of a shared file: the suggested text of the API, then the caption.
 * Never truncated here: the caller refuses a caption that does not fit.
 */
export function fileMessage(suggested: string, caption: string): string {
  const trimmed = caption.trim();
  return trimmed ? `${suggested} ${trimmed}` : suggested;
}

/** Bytes of the message for a caption, before the real link is known (link on `host`). */
export function estimatedFileMessageBytes(host: string, caption: string): number {
  const base = PREFIX_BYTES + shortFormBytes(host);
  const trimmed = caption.trim();
  return trimmed ? base + 1 + byteLength(trimmed) : base;
}

/** A message of `bytes` fits `limit` (null: none) the way the composer judges: strictly under. */
export function fitsLimit(bytes: number, limit: number | null): boolean {
  return limit === null || bytes < limit;
}
