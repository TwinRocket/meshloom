import { memo, type Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { Lock } from 'lucide-react';

import { cn } from '@/lib/utils';
import type { Channel, RawPacket } from '../../types';
import type { JournalEntry } from '../../utils/controlJournal';
import { labelPayloadType } from '../../utils/rawPacketLabels';
import {
  controlEnvelopeLabel,
  controlFromLabel,
  controlSubtypeLabel,
  controlToLabel,
  formatTypeHex,
  PeerName,
} from './controlParts';

/** What a control row asks of the feed; all optional so the row renders alone. */
export interface FeedControlActions {
  onInspect: (packet: RawPacket) => void;
  /** Contact sheet, from the peers (needs the full key, so only a unique match links). */
  onOpenContact?: (publicKey: string, fromChannel?: boolean) => void;
  /** GroupData channel link: the conversation, at its bottom. */
  onOpenChannel?: (channel: Channel) => void;
  /** "Paired" badge: scroll to the twin row. */
  onOpenPaired?: (pairedKey: string) => void;
}

interface FeedControlRowProps {
  entry: JournalEntry;
  time: { short: string; full: string };
  publicKey?: string;
  actions: FeedControlActions;
  /** The twin row is in the list, so the "Paired" badge can scroll to it. */
  pairedAvailable: boolean;
  highlighted: boolean;
  /** Display mode: not focusable, no inspector, no click. */
  readOnly?: boolean;
  measureRef?: Ref<HTMLDivElement>;
  index: number;
  offset: number;
}

const badgeClass =
  'rounded px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider bg-muted text-muted-foreground';

/**
 * One control packet of the timeline (Request, Response, AnonRequest, GroupData): type
 * and subtype badges, from → to, then the decrypted GroupData or the locked envelope.
 * Request / Anon payloads of third parties are never decrypted. A click opens the packet
 * inspector, as in the Control journal.
 */
export const FeedControlRow = memo(function FeedControlRow({
  entry,
  time,
  publicKey,
  actions,
  pairedAvailable,
  highlighted,
  readOnly = false,
  measureRef,
  index,
  offset,
}: FeedControlRowProps) {
  const { t } = useTranslation();
  const subtype = controlSubtypeLabel(t, entry);
  const envelope = controlEnvelopeLabel(t, entry);
  const channelName = entry.channel?.name ?? entry.packet.decrypted_info?.channel_name ?? null;
  const inspect = () => actions.onInspect(entry.packet);

  return (
    <div
      ref={measureRef}
      data-index={index}
      role="listitem"
      tabIndex={readOnly ? undefined : 0}
      aria-label={readOnly ? undefined : t('controlJournal.openInspector')}
      data-testid={`feed-control-row-${entry.journalType}`}
      className={cn(
        'absolute left-0 top-0 w-full border-b border-border/50 py-2 text-sm',
        !readOnly &&
          'cursor-pointer hover:bg-accent/30 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
        highlighted && 'bg-primary/10'
      )}
      style={{ transform: `translateY(${offset}px)` }}
      onClick={(event) => {
        if ((event.target as Element).closest('a,button,[role="link"]')) return;
        inspect();
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          inspect();
        }
      }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider text-primary">
          {labelPayloadType(entry.journalType)}
        </span>
        {subtype && <span className={badgeClass}>{subtype}</span>}
        {entry.packet.replayed && (
          <span data-testid="feed-control-replayed" className={badgeClass}>
            {t('feed.controlReplayed')}
          </span>
        )}
        {entry.pairedKey &&
          (pairedAvailable && actions.onOpenPaired ? (
            <button
              type="button"
              data-testid="feed-control-paired"
              className={cn(badgeClass, 'hover:bg-accent')}
              onClick={() => actions.onOpenPaired?.(entry.pairedKey as string)}
            >
              {t('controlJournal.paired')}
            </button>
          ) : (
            <span className={badgeClass}>{t('controlJournal.paired')}</span>
          ))}
        <time
          dateTime={new Date(entry.packet.timestamp * 1000).toISOString()}
          title={time.full}
          className="ml-auto text-xs text-muted-foreground"
        >
          {time.short}
        </time>
      </div>
      <p className="bidi-isolate mt-1">
        <PeerName
          label={controlFromLabel(t, entry, publicKey)}
          contact={entry.anonSender ?? entry.srcContacts[0]}
          unique={entry.anonSender != null || entry.srcContacts.length === 1}
          onOpenContactInfo={actions.onOpenContact}
        />
        <span className="text-muted-foreground"> → </span>
        <PeerName
          label={controlToLabel(t, entry)}
          contact={entry.destContacts[0]}
          unique={entry.destContacts.length === 1 && !entry.destIsUs}
          onOpenContactInfo={actions.onOpenContact}
        />
      </p>
      {entry.journalType === 'GroupData' && channelName && (
        <p className="mt-1 text-xs">
          {entry.channel && actions.onOpenChannel ? (
            <button
              type="button"
              data-testid="feed-control-channel"
              className="bidi-isolate text-primary hover:underline"
              onClick={() => actions.onOpenChannel?.(entry.channel as Channel)}
            >
              {t('controlJournal.channel', { name: channelName })}
            </button>
          ) : (
            <span data-testid="feed-control-channel" className="bidi-isolate text-muted-foreground">
              {t('controlJournal.channel', { name: channelName })}
            </span>
          )}
        </p>
      )}
      {entry.isOpen && entry.groupData ? (
        <div className="mt-1 text-sm">
          <p data-testid="feed-control-data-type">
            {t('controlJournal.dataType', { type: formatTypeHex(entry.groupData.data_type) })}
          </p>
          {entry.groupData.data_text && (
            <p className="bidi-isolate text-muted-foreground">{entry.groupData.data_text}</p>
          )}
        </div>
      ) : (
        <div className="mt-1 flex items-start gap-1.5 text-xs text-muted-foreground">
          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <div>
            <p>{t('controlJournal.locked')}</p>
            {envelope && <p data-testid="feed-control-envelope">{envelope}</p>}
          </div>
        </div>
      )}
    </div>
  );
});
