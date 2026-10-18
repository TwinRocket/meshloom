import { memo } from 'react';
import { useTranslation } from 'react-i18next';

import type { Message } from '../../types';
import { RegionBadge } from '../../utils/messageText';
import { MessageBody } from '../message/MessageBody';
import type { FeedRowLabels } from './feedNames';

interface FeedMessageRowProps {
  row: Message;
  labels: FeedRowLabels;
  time: { short: string; full: string };
  radioName?: string;
}

/**
 * One message of the timeline: where it came from (channel or contact), who wrote it,
 * when it was received, then the body through `MessageBody`. A MeshCore Open reaction
 * whose target is not loaded shows as its emoji, rendered by `MessageBody` too. Names
 * come from the mesh, so they are bidi-isolated; the sender's abbreviated key is the
 * hover text that tells two homonyms apart.
 */
export const FeedMessageRow = memo(function FeedMessageRow({
  row,
  labels,
  time,
  radioName,
}: FeedMessageRowProps) {
  const { t } = useTranslation();
  return (
    <>
      <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
        <time dateTime={new Date(row.received_at * 1000).toISOString()} title={time.full}>
          {time.short}
        </time>
        <span className="bidi-isolate min-w-0 truncate font-medium text-foreground/80">
          {labels.conversation}
        </span>
        {labels.sender && (
          <span
            className="bidi-isolate min-w-0 truncate"
            title={labels.senderKey ?? undefined}
            data-testid="feed-row-sender"
          >
            {labels.sender}
          </span>
        )}
        {row.region && <RegionBadge region={row.region} className="ml-0" />}
        {row.outgoing && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] uppercase tracking-wider">
            {t('feed.sent')}
          </span>
        )}
      </div>
      <MessageBody
        content={labels.content}
        radioName={radioName}
        remoteMedia={!labels.pendingChannel}
      />
    </>
  );
});
