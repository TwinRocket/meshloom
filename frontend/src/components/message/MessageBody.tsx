import { useRichPayloads } from '../../contexts/RichPayloadContext';
import { renderTextWithMentions } from '../../utils/messageText';
import { renderMeshcoreOpenPayload } from './richPayloads';

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
}

/**
 * The one place that renders a message's text and rich payloads (GIF, location,
 * reactions, links, mentions, #channel references). The conversation, search
 * results and the feed all go through it so a message looks the same everywhere.
 */
export function MessageBody({
  content,
  radioName,
  onChannelReferenceClick,
  remoteMedia = true,
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
    </div>
  );
}
