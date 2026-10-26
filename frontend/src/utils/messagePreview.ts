import {
  attachOpenReaction,
  openReactionHashSource,
  parseGif,
  parseLocation,
  parseMeshCoreOneReaction,
  parseReaction,
  richMessageBody,
  splitReplyMention,
  type ParsedReaction,
} from './meshcoreOpenPayloads';
import { parseSenderFromText } from './messageParser';
import i18n from '../i18n';
import { getShareStatusSnapshot } from '../stores/shareMetaStore';
import { DEFAULT_SHARE_HOSTS, parseShareLink, splitShareLinkBody } from './shareLinks';

const EXCERPT_MAX = 40;

export interface MessagePreviewContext {
  /** Last message was sent by this radio. */
  outgoing?: boolean;
  /** This radio's advertised name — matches a channel "Name: " prefix. */
  selfName?: string | null;
  /** Contact name, used for incoming DMs that carry no sender prefix. */
  conversationName?: string | null;
  /** Quoted body of the message that was reacted to, when known. */
  targetExcerpt?: string | null;
}

/**
 * What a conversation row should say the last message was.
 *
 * Rich payloads travel as ordinary plaintext — a GIF is `g:<id>`, a pin is
 * `m:<lat>,<lon>|…`, a reaction is `r:<hash>:<index>` — which the conversation
 * itself renders as a picture, a map or an emoji. The list showed the wire form,
 * so a conversation whose last message was a GIF read `g:APqEbxBsVIkWSuFpth`.
 *
 * Channel rows arrive as `"Name: body"`. The name is who sent it; the body is
 * what they sent. A reaction is rewritten as a sentence (who reacted, to what)
 * rather than left as `Alice: r:90fd:00`.
 *
 * Named here rather than in each list: the preview text is the same answer to the
 * same question wherever it is shown, and the server sends the raw body because it
 * has no notion of these formats — they are a convention between clients.
 */
export function describeMessagePreview(text: string, context: MessagePreviewContext = {}): string {
  const body = text.trim();
  // Nothing to describe: hand back exactly what was given, rather than a trimmed
  // version of it. A preview reports, it does not edit.
  if (!body) return text;

  const { sender, payload } = splitPreviewPayload(body);

  if (parseGif(payload)) {
    const gif = i18n.t('messagePreview.gif');
    return sender ? `${sender}: ${gif}` : gif;
  }

  const location = parseLocation(payload);
  if (location) {
    // The label is what the sender chose to call the place, so it says more than
    // the word "location" ever could.
    const label = location.label?.trim();
    const named = label
      ? i18n.t('messagePreview.locationNamed', { label })
      : i18n.t('messagePreview.location');
    return sender ? `${sender}: ${named}` : named;
  }

  const reaction = parseReaction(payload) ?? parseMeshCoreOneReaction(payload);
  if (reaction) return describeReactionPreview(reaction, sender, context);

  const share = describeSharePreview(payload);
  if (share) return sender ? `${sender}: ${share}` : share;

  return text;
}

/**
 * One line for a share message (an image, an encrypted file or a link), or null for any
 * other text. From the text alone: no network, no request for the link's meta, and the
 * link, its path and its `#fragment` (an encrypted file's key) are never in the line. A
 * link keeps its caption, if any. The Feed and the conversation list share it; the server
 * counterpart for push and e-mail is `app/share_text.py`.
 */
export function describeSharePreview(
  payload: string,
  hosts: readonly string[] = getShareStatusSnapshot()?.hosts ?? DEFAULT_SHARE_HOSTS
): string | null {
  const file = sharedFileKind(payload, hosts);
  if (file) {
    return file === 'image'
      ? `📷 ${i18n.t('messagePreview.shareImage')}`
      : `🔒 ${i18n.t('messagePreview.shareEncrypted')}`;
  }
  const shared = splitShareLinkBody(payload, hosts);
  if (shared) return `🔗 ${shared.caption || i18n.t('messagePreview.shareLink')}`;
  return null;
}

/**
 * A shared file message, from its text: `📷 <service link>` is an image, `🔒 <service
 * link>` an encrypted file (its key may follow the link as a `#fragment`). An optional
 * reply mention (`@[Name] `) comes first.
 */
function sharedFileKind(payload: string, hosts: readonly string[]): 'image' | 'encrypted' | null {
  const body = splitReplyMention(payload)?.body ?? payload;
  const match = /^(📷|🔒)\s+(\S+)/u.exec(body);
  if (!match) return null;
  const url = match[2].replace(/#.*$/, '');
  if (!parseShareLink(url, hosts)) return null;
  return match[1] === '📷' ? 'image' : 'encrypted';
}

const TIGHT_REACTION = /^(.+):(r:[0-9a-f]{4}:[0-9a-f]{2})$/;

/** Channel rows are `"Name: body"`. A few clients omit the space before `r:`. */
function splitPreviewPayload(text: string): { sender: string | null; payload: string } {
  const parsed = parseSenderFromText(text);
  if (parsed.sender) return { sender: parsed.sender, payload: parsed.content };
  const tight = TIGHT_REACTION.exec(text);
  if (tight && !parseReaction(text)) {
    return { sender: tight[1], payload: tight[2] };
  }
  return { sender: null, payload: text };
}

function describeReactionPreview(
  reaction: ParsedReaction,
  sender: string | null,
  context: MessagePreviewContext
): string {
  const you =
    context.outgoing === true || Boolean(sender && context.selfName && sender === context.selfName);
  const name = sender || (context.outgoing === false ? context.conversationName : null) || null;
  const excerpt = clipExcerpt(context.targetExcerpt);

  if (you && excerpt) {
    return i18n.t('messagePreview.youReactedTo', { emoji: reaction.emoji, excerpt });
  }
  if (you) {
    return i18n.t('messagePreview.youReacted', { emoji: reaction.emoji });
  }
  if (name && excerpt) {
    return i18n.t('messagePreview.theyReactedTo', { name, emoji: reaction.emoji, excerpt });
  }
  if (name) {
    return i18n.t('messagePreview.theyReacted', { name, emoji: reaction.emoji });
  }
  if (excerpt) {
    return i18n.t('messagePreview.reactedTo', { emoji: reaction.emoji, excerpt });
  }
  return i18n.t('messagePreview.reaction', { emoji: reaction.emoji });
}

export function clipExcerpt(text: string | null | undefined, max = EXCERPT_MAX): string | null {
  if (!text) return null;
  const oneLine = text.replace(/\s+/g, ' ').trim();
  if (!oneLine) return null;
  if (oneLine.length <= max) return oneLine;
  return `${oneLine.slice(0, max - 1).trimEnd()}…`;
}

/**
 * Quote the Open reaction target from a loaded conversation, if the last
 * preview text is an Open `r:HASH:INDEX` and a matching message is in `messages`.
 */
export function reactionTargetExcerptFromMessages(
  previewText: string,
  messages: readonly {
    type: 'PRIV' | 'CHAN';
    sender_timestamp: number | null;
    sender_name: string | null;
    text: string;
  }[]
): string | null {
  const { payload } = splitPreviewPayload(previewText.trim());
  const attached = attachOpenReaction(messages, payload, (message) =>
    openReactionHashSource(message)
  );
  if (!attached) return null;
  const target = messages[attached.targetIndex];
  const body = richMessageBody(target);
  if (parseGif(body)) return i18n.t('messagePreview.gif');
  const location = parseLocation(body);
  if (location) {
    const label = location.label?.trim();
    return label
      ? i18n.t('messagePreview.locationNamed', { label })
      : i18n.t('messagePreview.location');
  }
  return clipExcerpt(body);
}
