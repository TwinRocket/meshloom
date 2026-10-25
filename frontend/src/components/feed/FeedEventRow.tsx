import { memo, type Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { Hash, Pencil, UserPlus, Wifi, WifiOff, type LucideIcon } from 'lucide-react';

import { cn } from '@/lib/utils';
import type { FeedEvent, FeedEventId } from '../../utils/feedEvents';
import type { FeedEventView } from './feedEventText';

/** What an event row offers; every action is optional, a row without any is plain text. */
export interface FeedEventActions {
  /** Contact sheet beside the feed. */
  onOpenContact?: (publicKey: string) => void;
  /** The Discovered channels page, to adopt or refuse a found channel. */
  onOpenDiscovered?: () => void;
  /** The radio's settings, where its connection is shown. */
  onOpenRadio?: () => void;
}

const ICONS: Record<FeedEventId, LucideIcon> = {
  'radio.offline': WifiOff,
  'radio.online': Wifi,
  'contact.first_seen': UserPlus,
  'contact.renamed': Pencil,
  'channel.found': Hash,
};

interface FeedEventRowProps {
  event: FeedEvent;
  view: FeedEventView;
  time: { short: string; full: string };
  actions?: FeedEventActions;
  measureRef?: Ref<HTMLDivElement>;
  index: number;
  offset: number;
}

const actionClass =
  'rounded border border-border px-2 py-0.5 text-xs text-primary hover:bg-accent focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring';

/**
 * One event of the timeline (radio offline / online, a contact heard or renamed, a channel
 * found): an icon, one translated sentence, the time, and the actions the session may use.
 * A row without actions (display mode, a bare view, a role that cannot act) is plain text.
 */
export const FeedEventRow = memo(function FeedEventRow({
  event,
  view,
  time,
  actions,
  measureRef,
  index,
  offset,
}: FeedEventRowProps) {
  const { t } = useTranslation();
  const Icon = ICONS[event.id];
  const warn = event.id === 'radio.offline';
  const contactAction =
    view.contactKey && actions?.onOpenContact ? (
      <button
        type="button"
        data-testid="feed-event-open-contact"
        className={actionClass}
        onClick={() => actions.onOpenContact?.(view.contactKey as string)}
      >
        {t('feed.events.openContact')}
      </button>
    ) : null;
  const discoveredAction =
    view.pendingChannelKey && actions?.onOpenDiscovered ? (
      <button
        type="button"
        data-testid="feed-event-open-discovered"
        className={actionClass}
        onClick={() => actions.onOpenDiscovered?.()}
      >
        {t('feed.events.reviewChannel')}
      </button>
    ) : null;
  const radioAction =
    view.radio && actions?.onOpenRadio ? (
      <button
        type="button"
        data-testid="feed-event-open-radio"
        className={actionClass}
        onClick={() => actions.onOpenRadio?.()}
      >
        {t('feed.events.openRadio')}
      </button>
    ) : null;

  return (
    <div
      ref={measureRef}
      data-index={index}
      role="listitem"
      data-testid={`feed-event-row-${event.id}`}
      className="absolute left-0 top-0 w-full border-b border-border/50 py-2 text-sm"
      style={{ transform: `translateY(${offset}px)` }}
    >
      <div className="flex items-start gap-2">
        <Icon
          className={cn(
            'mt-0.5 h-4 w-4 shrink-0',
            warn ? 'text-destructive' : 'text-muted-foreground'
          )}
          aria-hidden="true"
        />
        <p className="bidi-isolate min-w-0 flex-1 break-words">{view.sentence}</p>
        <time
          dateTime={new Date(event.at * 1000).toISOString()}
          title={time.full}
          className="shrink-0 text-xs text-muted-foreground"
        >
          {time.short}
        </time>
      </div>
      {(contactAction || discoveredAction || radioAction) && (
        <div className="mt-1.5 flex flex-wrap gap-1.5 pl-6">
          {contactAction}
          {discoveredAction}
          {radioAction}
        </div>
      )}
    </div>
  );
});
