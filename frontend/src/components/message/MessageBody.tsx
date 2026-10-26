import { useRichPayloads } from '../../contexts/RichPayloadContext';
import { renderTextWithMentions } from '../../utils/messageText';
import { renderMeshcoreOpenPayload, SharePayload, type ShareMessageContext } from './richPayloads';

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
}: MessageBodyProps) {
  const { renderRichPayloads } = useRichPayloads();
  return (
    <div className="wrap-break-word whitespace-pre-wrap">
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
      <SharePayload
        content={content}
        message={message}
        privateConversation={privateConversation}
        remoteMedia={remoteMedia && !!message}
      />
    </div>
  );
}
