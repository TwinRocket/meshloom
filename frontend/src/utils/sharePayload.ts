import type { Channel, SharePreviewPolicy } from '../types';
import { splitReplyMention } from './meshcoreOpenPayloads';
import { isPublicChannelKey } from './publicChannel';
import { splitShareLinkBody } from './shareLinks';

/** Above this size (from the meta) an image is offered as a download, never inline. */
export const SHARE_INLINE_MAX_BYTES = 8 * 1024 * 1024;

/** Prefix the composer puts before an uploaded image link (`share_upload.suggested_text`). */
export const SHARE_IMAGE_PREFIX = '📷';

/**
 * The id of the shared image a message body carries, or null. Purely syntactic.
 *
 * The form is the composer's: `📷 <service link>[ caption]`, optionally behind a
 * reply mention (`@[Name] 📷 …`). A bare service link (a shortened URL) is not an
 * image and stays a plain anchor.
 */
export function shareImageId(content: string, hosts: readonly string[]): string | null {
  const trimmed = content.trim();
  const body = splitReplyMention(trimmed)?.body ?? trimmed;
  if (!body.startsWith(SHARE_IMAGE_PREFIX)) return null;
  const rest = body.slice(SHARE_IMAGE_PREFIX.length);
  if (!/^\s/.test(rest)) return null;
  return splitShareLinkBody(rest, hosts)?.id ?? null;
}

/**
 * A channel whose key is not the one derived from its name, so only people given
 * the key read it. Public (a well-known key) and `#name` channels are not private;
 * an unknown channel or an older backend without `key_derived` counts as not private.
 */
export function isPrivateChannel(
  channel: Pick<Channel, 'key' | 'key_derived'> | null | undefined
): boolean {
  return !!channel && channel.key_derived === false && !isPublicChannelKey(channel.key);
}

/** A DM, or a message of a private channel (looked up by key in `channels`). */
export function isPrivateConversation(
  msg: { type: 'PRIV' | 'CHAN'; conversation_key: string },
  channels: readonly Pick<Channel, 'key' | 'key_derived'>[]
): boolean {
  if (msg.type === 'PRIV') return true;
  const key = msg.conversation_key.toUpperCase();
  return isPrivateChannel(channels.find((c) => c.key.toUpperCase() === key));
}

/**
 * Whether a shared image may load without a tap. `remoteMedia: false` from the parent
 * and a blocked sender always mean a tap; then the preview policy decides.
 */
export function shareMediaAutoLoads(options: {
  policy: SharePreviewPolicy | undefined;
  privateConversation: boolean;
  remoteMedia: boolean;
  blocked: boolean;
}): boolean {
  if (!options.remoteMedia || options.blocked) return false;
  if (options.policy === 'always') return true;
  if (options.policy === 'private') return options.privateConversation;
  return false;
}
