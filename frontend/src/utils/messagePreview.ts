import { parseGif, parseLocation, parseReaction } from './meshcoreOpenPayloads';
import i18n from '../i18n';

/**
 * What a conversation row should say the last message was.
 *
 * Rich payloads travel as ordinary plaintext — a GIF is `g:<id>`, a pin is
 * `m:<lat>,<lon>|…`, a reaction is `r:<hash>:<index>` — which the conversation
 * itself renders as a picture, a map or an emoji. The list showed the wire form,
 * so a conversation whose last message was a GIF read `g:APqEbxBsVIkWSuFpth`.
 *
 * Named here rather than in each list: the preview text is the same answer to the
 * same question wherever it is shown, and the server sends the raw body because it
 * has no notion of these formats — they are a convention between clients.
 */
export function describeMessagePreview(text: string): string {
  const body = text.trim();
  // Nothing to describe: hand back exactly what was given, rather than a trimmed
  // version of it. A preview reports, it does not edit.
  if (!body) return text;

  if (parseGif(body)) return i18n.t('messagePreview.gif');

  const location = parseLocation(body);
  if (location) {
    // The label is what the sender chose to call the place, so it says more than
    // the word "location" ever could.
    const label = location.label?.trim();
    return label
      ? i18n.t('messagePreview.locationNamed', { label })
      : i18n.t('messagePreview.location');
  }

  const reaction = parseReaction(body);
  if (reaction) return i18n.t('messagePreview.reaction', { emoji: reaction.emoji });

  return text;
}
