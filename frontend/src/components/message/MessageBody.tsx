import { useCallback } from 'react';
import { useRichPayloads } from '../../contexts/RichPayloadContext';
import { ShareLinkGateContext } from '../../contexts/ShareLinkGateContext';
import { renderTextWithMentions } from '../../utils/messageText';
import {
  renderMeshcoreOpenPayload,
  SharePayload,
  useShareImage,
  type ShareMessageContext,
} from './richPayloads';

interface MessageBodyProps {
  /** Message text without the channel "Sender: " prefix. */
  content: string;
  radioName?: string;
  onChannelReferenceClick?: (channelName: string) => void;
  /**
   * Allow the body to load third-party media (a Giphy GIF). When false a GIF stays
   * its raw text. Defaults to true, the conversation behaviour.
   */
  remoteMedia?: boolean;
  /**
   * The message, for a shared image: its id (`?message_id=`) and the sender, matched
   * against the block lists. Without it a shared image never loads on its own.
   */
  message?: ShareMessageContext;
  /**
   * A DM or a private channel (key not derived from the name): the `private` preview
   * policy loads a shared image there without a tap. Defaults to false.
   */
  privateConversation?: boolean;
  /**
   * False on a screen nobody interacts with (Feed display mode): a shared image that
   * may not load shows a plain label, not a tap button. Defaults to true.
   */
  interactive?: boolean;
}

/**
 * The one place that renders a message's text and rich payloads (GIF, location,
 * reactions, links, mentions, #channel references, shared images). The conversation, search
 * results and the feed all go through it so a message looks the same everywhere.
 */
export function MessageBody({
  content,
  radioName,
  onChannelReferenceClick,
  remoteMedia = true,
  message,
  privateConversation = false,
  interactive = true,
}: MessageBodyProps) {
  const { renderRichPayloads } = useRichPayloads();
  const image = useShareImage({ content, message, privateConversation, remoteMedia });
  const imageId = image?.id ?? null;
  const imageLoads = image?.load ?? false;
  // The visible link of a shared image must not warm its meta before the image may load.
  const mayPrefetch = useCallback(
    (id: string) => id !== imageId || imageLoads,
    [imageId, imageLoads]
  );
  return (
    <div className="wrap-break-word whitespace-pre-wrap">
      <ShareLinkGateContext.Provider value={mayPrefetch}>
        {renderMeshcoreOpenPayload(
          content,
          radioName,
          onChannelReferenceClick,
          renderRichPayloads,
          remoteMedia
        ) ||
          content.split('\n').map((line, i, arr) => (
            <span key={i}>
              {renderTextWithMentions(line, radioName, onChannelReferenceClick)}
              {i < arr.length - 1 && <br />}
            </span>
          ))}
      </ShareLinkGateContext.Provider>
      <SharePayload image={image} message={message} interactive={interactive} />
    </div>
  );
}
