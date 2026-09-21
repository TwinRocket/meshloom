import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  MeshCoreDecoder,
  PayloadType,
  Utils,
  type AnonRequestPayload,
  type RequestPayload,
} from '@michaelhart/meshcore-decoder';
import { Lock } from 'lucide-react';

import type { Channel, Contact, Conversation, RawPacket } from '../types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from '../types';
import { useRawPackets } from '../stores/rawPacketStore';
import { getRawPacketObservationKey } from '../utils/rawPacketIdentity';
import { getPacketTypeName } from '../utils/rawPacketStats';
import { labelPayloadType } from '../utils/rawPacketLabels';
import { collectGroupDataKeys, resolveGroupData } from '../utils/rawPacketInspector';
import { parseGroupData } from '../utils/parseGroupData';
import { getContactDisplayName } from '../utils/pubkey';
import { resolveContactsByPrefix } from './RawPacketList';
import { RawPacketInspectorDialog } from './RawPacketDetailModal';
import { ToolPaneHeader } from './ToolPaneHeader';
import { cn } from '../lib/utils';

export const CONTROL_JOURNAL_PAYLOAD_TYPES = [
  'Request',
  'Response',
  'AnonRequest',
  'GroupData',
] as const;

export type ControlJournalPayloadType = (typeof CONTROL_JOURNAL_PAYLOAD_TYPES)[number];

const JOURNAL_TYPE_FOLD: Record<string, ControlJournalPayloadType> = {
  request: 'Request',
  req: 'Request',
  response: 'Response',
  resp: 'Response',
  anonrequest: 'AnonRequest',
  anonreq: 'AnonRequest',
  groupdata: 'GroupData',
  grpdata: 'GroupData',
};

export function foldPayloadType(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, '');
}

export function canonicalControlJournalType(payloadType: string): ControlJournalPayloadType | null {
  return JOURNAL_TYPE_FOLD[foldPayloadType(payloadType)] ?? null;
}

export function isControlJournalPayloadType(payloadType: string): boolean {
  return canonicalControlJournalType(payloadType) !== null;
}

export function destMatchesPublicKey(destHash: string, publicKey: string): boolean {
  const dest = destHash.replace(/^0x/i, '').replace(/\s+/g, '').toLowerCase();
  const key = publicKey.replace(/^0x/i, '').replace(/\s+/g, '').toLowerCase();
  if (!dest || !key) return false;
  if (key.startsWith(dest)) return true;
  const firstByte = key.slice(0, 2);
  const prefix = key.slice(0, 12);
  return dest === firstByte || dest === prefix;
}

export function filterControlJournalPackets(packets: RawPacket[]): RawPacket[] {
  return packets.filter((packet) => {
    const decodedName = getPacketTypeName(packet);
    return (
      isControlJournalPayloadType(decodedName) || isControlJournalPayloadType(packet.payload_type)
    );
  });
}

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

function isRepeaterOrRoom(contact: Contact): boolean {
  return contact.type === CONTACT_TYPE_REPEATER || contact.type === CONTACT_TYPE_ROOM;
}

function findContactByKey(publicKey: string, contacts: Contact[]): Contact | undefined {
  const lower = publicKey.toLowerCase();
  return contacts.find((contact) => contact.public_key.toLowerCase() === lower);
}

function extractCorrelationTag(
  decoded: ReturnType<typeof MeshCoreDecoder.decode> | null
): string | null {
  if (!decoded?.isValid || !decoded.payload.decoded) return null;
  const payload = decoded.payload.decoded as {
    tag?: number;
    traceTag?: string;
    decrypted?: { tag?: number };
  };
  if (typeof payload.decrypted?.tag === 'number') {
    return payload.decrypted.tag.toString(16);
  }
  if (typeof payload.tag === 'number') {
    return payload.tag.toString(16);
  }
  if (typeof payload.traceTag === 'string' && payload.traceTag.trim()) {
    return payload.traceTag.trim();
  }
  return null;
}

function resolveGroupDataChannel(
  packet: RawPacket,
  channels: Channel[],
  keys: string[]
): Channel | null {
  const infoKey = packet.decrypted_info?.channel_key?.trim();
  if (infoKey) {
    return channels.find((channel) => channel.key.toLowerCase() === infoKey.toLowerCase()) ?? null;
  }
  if (keys.length === 0) return null;
  for (const channel of channels) {
    if (parseGroupData(packet.data, [channel.key])) {
      return channel;
    }
  }
  return null;
}

type ThreadKind = 'repeater' | 'channel' | 'us' | 'unresolved';

interface JournalEntry {
  packet: RawPacket;
  observationKey: string;
  journalType: ControlJournalPayloadType;
  destHash: string | null;
  srcHash: string | null;
  destContacts: Contact[];
  srcContacts: Contact[];
  destIsUs: boolean;
  anonSenderKey: string | null;
  anonSender: Contact | null;
  requestSubtype: string | null;
  correlationTag: string | null;
  pairedKey: string | null;
  groupData: ReturnType<typeof resolveGroupData>;
  channel: Channel | null;
  isOpen: boolean;
  threadId: string;
  threadKind: ThreadKind;
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

function classifyThread(
  entry: Omit<JournalEntry, 'threadId' | 'threadKind' | 'pairedKey'>,
  publicKey?: string
): Pick<JournalEntry, 'threadId' | 'threadKind'> {
  if (entry.journalType === 'GroupData') {
    if (entry.channel) {
      return { threadKind: 'channel', threadId: `channel:${entry.channel.key}` };
    }
    return { threadKind: 'channel', threadId: 'channel:unresolved' };
  }

  const destMatches = entry.destContacts;
  const uniqueDest = destMatches.length === 1 ? destMatches[0] : null;
  const destAmbiguousWithUs =
    entry.destIsUs && destMatches.some((contact) => contact.public_key !== publicKey);
  const destAmbiguousContacts = destMatches.length > 1;

  if (entry.destIsUs && !destAmbiguousWithUs && !destAmbiguousContacts) {
    return { threadKind: 'us', threadId: 'us' };
  }
  if (uniqueDest && isRepeaterOrRoom(uniqueDest) && !entry.destIsUs) {
    return { threadKind: 'repeater', threadId: `contact:${uniqueDest.public_key}` };
  }
  if (uniqueDest && isRepeaterOrRoom(uniqueDest) && entry.destIsUs) {
    return { threadKind: 'unresolved', threadId: 'unresolved' };
  }
  if (entry.destIsUs && !destAmbiguousContacts) {
    return { threadKind: 'us', threadId: 'us' };
  }
  return { threadKind: 'unresolved', threadId: 'unresolved' };
}

function buildJournalEntries(
  packets: RawPacket[],
  contacts: Contact[],
  channels: Channel[],
  publicKey?: string
): JournalEntry[] {
  const keys = collectGroupDataKeys(channels);
  const built: JournalEntry[] = [];

  for (const packet of packets) {
    const decodedName = getPacketTypeName(packet);
    const journalType =
      canonicalControlJournalType(decodedName) ?? canonicalControlJournalType(packet.payload_type);
    if (!journalType) continue;

    let decoded: ReturnType<typeof MeshCoreDecoder.decode> | null = null;
    try {
      decoded = MeshCoreDecoder.decode(packet.data);
    } catch {
      decoded = null;
    }

    let destHash: string | null = null;
    let srcHash: string | null = null;
    let anonSenderKey: string | null = null;
    let requestSubtype: string | null = null;
    const payload = decoded?.isValid ? decoded.payload.decoded : null;

    if (payload) {
      const hashed = payload as { destinationHash?: string; sourceHash?: string };
      if (hashed.destinationHash) destHash = hashed.destinationHash.toUpperCase();
      if (hashed.sourceHash) srcHash = hashed.sourceHash.toUpperCase();
    }

    if (decoded?.isValid && decoded.payloadType === PayloadType.AnonRequest && payload) {
      const anon = payload as AnonRequestPayload;
      if (anon.senderPublicKey) {
        anonSenderKey = anon.senderPublicKey.toLowerCase();
      }
    }

    if (decoded?.isValid && decoded.payloadType === PayloadType.Request && payload) {
      const request = payload as RequestPayload;
      if (request.decrypted?.requestType != null) {
        requestSubtype = Utils.getRequestTypeName(request.decrypted.requestType);
      }
    }

    const destContacts = destHash ? resolveContactsByPrefix(destHash, contacts) : [];
    const srcContacts = srcHash ? resolveContactsByPrefix(srcHash, contacts) : [];
    const destIsUs = Boolean(destHash && publicKey && destMatchesPublicKey(destHash, publicKey));
    const anonSender = anonSenderKey ? (findContactByKey(anonSenderKey, contacts) ?? null) : null;
    const groupData = resolveGroupData(packet, decoded?.isValid ? decoded.payloadType : null, keys);
    const channel = resolveGroupDataChannel(packet, channels, keys);
    const isOpen = journalType === 'GroupData' && groupData !== null;
    const correlationTag = extractCorrelationTag(decoded);

    const partial = {
      packet,
      observationKey: getRawPacketObservationKey(packet),
      journalType,
      destHash,
      srcHash,
      destContacts,
      srcContacts,
      destIsUs,
      anonSenderKey,
      anonSender,
      requestSubtype,
      correlationTag,
      groupData,
      channel,
      isOpen,
    };
    const thread = classifyThread(partial, publicKey);
    built.push({ ...partial, pairedKey: null, ...thread });
  }

  const tagOwners = new Map<string, JournalEntry[]>();
  for (const entry of built) {
    if (!entry.correlationTag) continue;
    const bucket = tagOwners.get(entry.correlationTag) ?? [];
    bucket.push(entry);
    tagOwners.set(entry.correlationTag, bucket);
  }
  for (const group of tagOwners.values()) {
    const requests = group.filter((entry) => entry.journalType === 'Request');
    const responses = group.filter((entry) => entry.journalType === 'Response');
    if (requests.length === 0 || responses.length === 0) continue;
    for (const request of requests) {
      request.pairedKey = responses[0].observationKey;
    }
    for (const response of responses) {
      response.pairedKey = requests[0].observationKey;
    }
  }

  return built;
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

  useEffect(() => {
    if (selectedThreadId && threads.some((thread) => thread.id === selectedThreadId)) {
      return;
    }
    setSelectedThreadId(threads[0]?.id ?? null);
  }, [selectedThreadId, threads]);

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
              <div className="mx-auto flex w-full max-w-none flex-col gap-2 lg:max-w-[52rem] 2xl:max-w-[64rem]">
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
                      className="w-full cursor-pointer rounded-lg border border-border bg-card px-3 py-2.5 text-left shadow-sm transition-colors hover:border-primary/40"
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
