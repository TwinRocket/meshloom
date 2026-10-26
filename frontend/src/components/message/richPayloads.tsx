import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { MapPin } from 'lucide-react';
import {
  giphyUrlForId,
  locationMapUrl,
  parseGif,
  parseLocation,
  parseMeshCoreOneReaction,
  parseReaction,
  splitReplyMention,
} from '../../utils/meshcoreOpenPayloads';
import { renderTextWithMentions } from '../../utils/messageText';

// Service links are rendered by the anchor linkifyText uses; exposed here with the other payloads.
export { ShareLinkAnchor } from './ShareLinkAnchor';
// A shared image (`📷 <service link>`) renders after the text, under the preview policy.
export { SharePayload, useShareImage, type ShareMessageContext } from './SharePayload';

// Renders a MeshCore Open GIF payload, falling back to the raw text on load error.
function GifPayload({ gifId, rawText }: { gifId: string; rawText: string }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <>{rawText}</>;
  }
  const url = giphyUrlForId(gifId);
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-block"
      title={t('messageList.openGif')}
    >
      <img
        src={url}
        alt={t('messageList.gifAlt')}
        data-testid="message-gif"
        loading="lazy"
        onError={() => setFailed(true)}
        className="max-w-[240px] max-h-[240px] rounded-md"
      />
    </a>
  );
}

function LocationPayload({ lat, lon, label }: { lat: number; lon: number; label: string }) {
  const { t } = useTranslation();
  const url = locationMapUrl(lat, lon);
  const coords = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      data-testid="message-location"
      className="inline-flex items-center gap-2 rounded-md border border-border bg-muted/40 px-2.5 py-1.5 hover:bg-muted/70"
      title={t('messageList.openLocation')}
    >
      <MapPin className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
      <span className="min-w-0">
        <span className="block text-sm font-medium leading-tight">
          {label || t('messageList.locationAlt')}
        </span>
        <span className="block text-[0.6875rem] text-muted-foreground tabular-nums">{coords}</span>
      </span>
    </a>
  );
}

// Renders a reaction generically (emoji + "reacted"); the target message is not
// resolved (see issues #291 and #354). MeshCore One reactions name the target's
// sender, so show that when it is there.
function ReactionPayload({ emoji, targetSender }: { emoji: string; targetSender?: string }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-xl leading-none">{emoji}</span>
      <span className="text-xs text-muted-foreground italic">
        {targetSender
          ? t('messageList.reactedTo', { sender: targetSender })
          : t('messageList.reacted')}
      </span>
    </span>
  );
}

// Render a bare payload body (no reply prefix) into its rich node, or null.
// GIFs always render — the compact `g:<id>` form is otherwise unreadable, and
// inbound Open GIFs would otherwise stay hidden behind the reactions toggle.
export function renderPayloadBody(
  body: string,
  allowReactions: boolean,
  remoteMedia = true
): ReactNode | null {
  const gifId = parseGif(body);
  if (gifId) {
    // Without remote media the GIF stays its raw text instead of loading media.giphy.com.
    if (!remoteMedia) return <>{body}</>;
    return <GifPayload gifId={gifId} rawText={body} />;
  }
  const location = parseLocation(body);
  if (location) {
    return <LocationPayload lat={location.lat} lon={location.lon} label={location.label} />;
  }
  if (!allowReactions) return null;
  const reaction = parseReaction(body) ?? parseMeshCoreOneReaction(body);
  if (reaction) {
    return <ReactionPayload emoji={reaction.emoji} targetSender={reaction.targetSender} />;
  }
  return null;
}

// Recognize a MeshCore Open payload and render it. Handles both a whole-message
// payload ("g:<id>") and a reply-prefixed one ("@[Name] g:<id>") — the form
// meshcore-open sends when a GIF/reaction is a reply, which otherwise renders as
// raw text (issue #291). Returns null when the content is not a recognized
// payload, so the caller renders normally.
export function renderMeshcoreOpenPayload(
  content: string,
  radioName?: string,
  onChannelReferenceClick?: (channelName: string) => void,
  allowReactions = false,
  remoteMedia = true
): ReactNode | null {
  const whole = renderPayloadBody(content, allowReactions, remoteMedia);
  if (whole) return whole;

  const split = splitReplyMention(content);
  if (split) {
    const body = renderPayloadBody(split.body, allowReactions, remoteMedia);
    if (body) {
      // Preserve the reply mention (rendered as a normal @[Name] mention) so the
      // GIF/reaction still reads as a reply to that person.
      return (
        <span className="inline-flex flex-wrap items-center gap-1.5">
          {renderTextWithMentions(split.mention, radioName, onChannelReferenceClick)}
          {body}
        </span>
      );
    }
  }
  return null;
}
