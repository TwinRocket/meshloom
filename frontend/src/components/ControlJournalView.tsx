import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lock } from 'lucide-react';

import type { Channel, Contact, Conversation, RawPacket } from '../types';
import { useRawPackets } from '../stores/rawPacketStore';
import { labelPayloadType } from '../utils/rawPacketLabels';
import { getContactDisplayName } from '../utils/pubkey';
import {
  buildJournalEntries,
  destMatchesPublicKey,
  filterControlJournalPackets,
  type JournalEntry,
  type ThreadKind,
} from '../utils/controlJournal';
import { RawPacketInspectorDialog } from './RawPacketDetailModal';
import { ToolPaneHeader } from './ToolPaneHeader';
import { cn } from '../lib/utils';

function formatTypeHex(value: number): string {
  return `0x${value.toString(16).padStart(4, '0')}`;
}

function formatTime(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

interface JournalThread {
  id: string;
  kind: ThreadKind;
  label: string;
  contact?: Contact;
  channel?: Channel;
  entries: JournalEntry[];
  lastTimestamp: number;
}

function peerLabel(
  contacts: Contact[],
  hash: string | null,
  isUs: boolean,
  usLabel: string,
  unknownLabel: string,
  ambiguousLabel: (hash: string) => string
): string {
  if (isUs) return usLabel;
  if (!hash) return unknownLabel;
  if (contacts.length === 1) {
    return getContactDisplayName(contacts[0].name, contacts[0].public_key, contacts[0].last_advert);
  }
  if (contacts.length > 1) return ambiguousLabel(hash);
  return hash;
}

function PeerName({
  label,
  contact,
  unique,
  onOpenContactInfo,
}: {
  label: string;
  contact?: Contact;
  unique: boolean;
  onOpenContactInfo: (publicKey: string, fromChannel?: boolean) => void;
}) {
  if (!unique || !contact) {
    return <span>{label}</span>;
  }
  return (
    <span
      role="link"
      tabIndex={0}
      className="cursor-pointer text-primary hover:underline"
      onClick={(event) => {
        event.stopPropagation();
        onOpenContactInfo(contact.public_key);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          onOpenContactInfo(contact.public_key);
        }
      }}
    >
      {label}
    </span>
  );
}

interface ControlJournalViewProps {
  contacts: Contact[];
  channels: Channel[];
  onOpenContactInfo: (publicKey: string, fromChannel?: boolean) => void;
  onSelectConversation: (conversation: Conversation) => void;
  publicKey?: string;
  onBackToTools?: () => void;
}

export function ControlJournalView({
  contacts,
  channels,
  onOpenContactInfo,
  onSelectConversation,
  publicKey,
  onBackToTools,
}: ControlJournalViewProps) {
  const { t } = useTranslation();
  const livePackets = useRawPackets();
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [selectedPacket, setSelectedPacket] = useState<RawPacket | null>(null);

  const journalPackets = useMemo(() => filterControlJournalPackets(livePackets), [livePackets]);
  const entries = useMemo(
    () => buildJournalEntries(journalPackets, contacts, channels, publicKey),
    [journalPackets, contacts, channels, publicKey]
  );

  const threads = useMemo(() => {
    const byId = new Map<string, JournalThread>();
    for (const entry of entries) {
      const existing = byId.get(entry.threadId);
      if (existing) {
        existing.entries.push(entry);
        existing.lastTimestamp = Math.max(existing.lastTimestamp, entry.packet.timestamp);
        continue;
      }
      let label = t('controlJournal.unresolvedThread');
      if (entry.threadKind === 'us') label = t('controlJournal.usThread');
      if (entry.threadKind === 'channel') {
        label = entry.channel?.name ?? t('controlJournal.unresolvedThread');
      }
      if (entry.threadKind === 'repeater' && entry.destContacts[0]) {
        const contact = entry.destContacts[0];
        label = getContactDisplayName(contact.name, contact.public_key, contact.last_advert);
      }
      byId.set(entry.threadId, {
        id: entry.threadId,
        kind: entry.threadKind,
        label,
        contact: entry.threadKind === 'repeater' ? entry.destContacts[0] : undefined,
        channel: entry.channel ?? undefined,
        entries: [entry],
        lastTimestamp: entry.packet.timestamp,
      });
    }
    for (const thread of byId.values()) {
      thread.entries.sort((a, b) => a.packet.timestamp - b.packet.timestamp);
    }
    return [...byId.values()].sort((a, b) => b.lastTimestamp - a.lastTimestamp);
  }, [entries, t]);

  const sections = useMemo(() => {
    return [
      {
        kind: 'repeater' as const,
        title: t('controlJournal.sectionRepeaters'),
        threads: threads.filter((thread) => thread.kind === 'repeater'),
      },
      {
        kind: 'channel' as const,
        title: t('controlJournal.sectionChannels'),
        threads: threads.filter((thread) => thread.kind === 'channel'),
      },
      {
        kind: 'us' as const,
        title: t('controlJournal.sectionUs'),
        threads: threads.filter((thread) => thread.kind === 'us'),
      },
      {
        kind: 'unresolved' as const,
        title: t('controlJournal.sectionUnresolved'),
        threads: threads.filter((thread) => thread.kind === 'unresolved'),
      },
    ].filter((section) => section.threads.length > 0);
  }, [t, threads]);

  // Fall back to the first thread when nothing valid is selected; the fallback is
  // stored, so it stays put when newer threads arrive (adjust state during render).
  if (!selectedThreadId || !threads.some((thread) => thread.id === selectedThreadId)) {
    const fallbackThreadId = threads[0]?.id ?? null;
    if (fallbackThreadId !== selectedThreadId) setSelectedThreadId(fallbackThreadId);
  }

  const activeThread = threads.find((thread) => thread.id === selectedThreadId) ?? null;

  const subtypeLabel = (entry: JournalEntry): string | null => {
    if (entry.journalType === 'AnonRequest') return t('controlJournal.subtypeLogin');
    if (entry.journalType === 'GroupData') return t('controlJournal.subtypeDatagram');
    if (!entry.requestSubtype) return null;
    const folded = entry.requestSubtype.toLowerCase().replace(/[\s_()-]+/g, '');
    if (folded.includes('stats')) return t('controlJournal.subtypeStatus');
    if (folded.includes('owner')) return t('controlJournal.subtypeOwner');
    if (folded.includes('region')) return t('controlJournal.subtypeRegions');
    return entry.requestSubtype;
  };

  const fromLabel = (entry: JournalEntry): string => {
    if (entry.anonSender) {
      return getContactDisplayName(
        entry.anonSender.name,
        entry.anonSender.public_key,
        entry.anonSender.last_advert
      );
    }
    if (entry.anonSenderKey) {
      return entry.anonSenderKey.slice(0, 12).toUpperCase();
    }
    return peerLabel(
      entry.srcContacts,
      entry.srcHash,
      Boolean(entry.srcHash && publicKey && destMatchesPublicKey(entry.srcHash, publicKey)),
      t('controlJournal.usThread'),
      t('controlJournal.unknownPeer'),
      (hash) => t('controlJournal.ambiguousHash', { hash })
    );
  };

  const toLabel = (entry: JournalEntry): string => {
    return peerLabel(
      entry.destContacts,
      entry.destHash,
      entry.destIsUs,
      t('controlJournal.usThread'),
      t('controlJournal.unknownPeer'),
      (hash) => t('controlJournal.ambiguousHash', { hash })
    );
  };

  return (
    <div data-testid="control-journal" className="flex min-h-0 flex-1 flex-col bg-background">
      <ToolPaneHeader title={t('controlJournal.title')} onBack={onBackToTools} />
      {entries.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-muted-foreground">
          {t('controlJournal.empty')}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <nav
            aria-label={t('controlJournal.threadsAria')}
            className="shrink-0 overflow-y-auto border-b border-border md:w-64 md:border-b-0 md:border-r"
          >
            {sections.map((section) => (
              <div key={section.kind} className="px-3 py-2">
                <h3 className="px-1 text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                  {section.title}
                </h3>
                <ul className="mt-1 space-y-0.5">
                  {section.threads.map((thread) => {
                    const selected = thread.id === activeThread?.id;
                    return (
                      <li key={thread.id}>
                        <button
                          type="button"
                          data-testid={`control-journal-thread-${thread.kind}`}
                          aria-pressed={selected}
                          onClick={() => setSelectedThreadId(thread.id)}
                          className={cn(
                            'flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm',
                            selected
                              ? 'bg-primary/10 text-primary'
                              : 'hover:bg-muted text-foreground'
                          )}
                        >
                          <span className="min-w-0 truncate">{thread.label}</span>
                          <span className="ml-2 shrink-0 text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                            {t('controlJournal.packetCount', { count: thread.entries.length })}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>
          <div
            role="list"
            aria-label={t('controlJournal.cardsAria')}
            className="min-h-0 flex-1 overflow-y-auto px-3 py-3"
          >
            {!activeThread ? (
              <p className="px-2 text-sm text-muted-foreground">
                {t('controlJournal.selectThread')}
              </p>
            ) : (
              <div className="mx-auto flex w-full max-w-none flex-col gap-2 lg:max-w-208 2xl:max-w-5xl">
                {activeThread.entries.map((entry) => {
                  const subtype = subtypeLabel(entry);
                  const destSrc =
                    entry.destHash && entry.srcHash
                      ? t('controlJournal.destSrc', { dest: entry.destHash, src: entry.srcHash })
                      : entry.destHash
                        ? t('controlJournal.destOnly', { dest: entry.destHash })
                        : entry.srcHash
                          ? t('controlJournal.srcOnly', { src: entry.srcHash })
                          : null;
                  const channelName =
                    entry.channel?.name ?? entry.packet.decrypted_info?.channel_name ?? null;
                  return (
                    <article
                      key={entry.observationKey}
                      role="listitem"
                      tabIndex={0}
                      data-testid={`control-journal-card-${entry.journalType}`}
                      aria-label={t('controlJournal.openInspector')}
                      onClick={() => setSelectedPacket(entry.packet)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          setSelectedPacket(entry.packet);
                        }
                      }}
                      className="w-full cursor-pointer rounded-lg border border-border bg-card px-3 py-2.5 text-left shadow-xs transition-colors hover:border-primary/40"
                    >
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider text-primary">
                          {labelPayloadType(entry.journalType)}
                        </span>
                        {subtype ? (
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                            {subtype}
                          </span>
                        ) : null}
                        {entry.pairedKey ? (
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                            {t('controlJournal.paired')}
                          </span>
                        ) : null}
                        <span className="ml-auto text-xs text-muted-foreground">
                          {formatTime(entry.packet.timestamp)}
                        </span>
                      </div>
                      <p className="mt-1.5 text-sm">
                        <PeerName
                          label={fromLabel(entry)}
                          contact={entry.anonSender ?? entry.srcContacts[0]}
                          unique={entry.anonSender != null || entry.srcContacts.length === 1}
                          onOpenContactInfo={onOpenContactInfo}
                        />
                        <span className="text-muted-foreground"> → </span>
                        <PeerName
                          label={toLabel(entry)}
                          contact={entry.destContacts[0]}
                          unique={entry.destContacts.length === 1 && !entry.destIsUs}
                          onOpenContactInfo={onOpenContactInfo}
                        />
                      </p>
                      {entry.journalType === 'GroupData' && channelName ? (
                        <p
                          className={cn(
                            'mt-1 text-xs',
                            entry.channel
                              ? 'cursor-pointer text-primary hover:underline'
                              : 'text-muted-foreground'
                          )}
                          data-testid="control-journal-channel"
                          onClick={(event) => {
                            if (!entry.channel) return;
                            event.stopPropagation();
                            onSelectConversation({
                              type: 'channel',
                              id: entry.channel.key,
                              name: entry.channel.name,
                            });
                          }}
                        >
                          {t('controlJournal.channel', { name: channelName })}
                        </p>
                      ) : null}
                      {entry.isOpen && entry.groupData ? (
                        <div className="mt-1.5 text-sm">
                          <p data-testid="control-journal-data-type">
                            {t('controlJournal.dataType', {
                              type: formatTypeHex(entry.groupData.data_type),
                            })}
                          </p>
                          {entry.groupData.data_text ? (
                            <p className="text-muted-foreground">{entry.groupData.data_text}</p>
                          ) : null}
                        </div>
                      ) : (
                        <div className="mt-1.5 flex items-start gap-1.5 text-xs text-muted-foreground">
                          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                          <div>
                            <p>{t('controlJournal.locked')}</p>
                            {destSrc ? (
                              <p data-testid="control-journal-envelope">{destSrc}</p>
                            ) : null}
                          </div>
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
      <RawPacketInspectorDialog
        open={selectedPacket !== null}
        onOpenChange={(isOpen) => !isOpen && setSelectedPacket(null)}
        channels={channels}
        source={
          selectedPacket
            ? { kind: 'packet', packet: selectedPacket }
            : { kind: 'loading', message: t('rawPacket.loading') }
        }
        title={t('rawPacket.details')}
        description={t('rawPacket.detailsDescription')}
      />
    </div>
  );
}
