import {
  MeshCoreDecoder,
  PayloadType,
  Utils,
  type AnonRequestPayload,
  type RequestPayload,
} from '@michaelhart/meshcore-decoder';

import type { Channel, Contact, RawPacket } from '../types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from '../types';
import { getRawPacketObservationKey } from './rawPacketIdentity';
import { getDecodedPayloadTypeName } from './rawPacketStats';
import { collectGroupDataKeys, resolveGroupData } from './rawPacketInspector';
import { parseGroupData } from './parseGroupData';
import { resolveContactsByPrefix } from './pubkey';

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

type DecodedPacket = ReturnType<typeof MeshCoreDecoder.decode>;

/** What one `MeshCoreDecoder.decode()` call yields; independent of contacts and channels. */
interface DecodeFacts {
  /** The bytes that were decoded, to tell a reused observation key from the same packet. */
  data: string;
  decoded: DecodedPacket | null;
  /** `getPacketTypeName` result: known payload type name, else `Unknown`. */
  typeName: string;
}

/**
 * Decode cache, one entry per observation key. Holds at most the live buffer
 * (`MAX_RAW_PACKETS`): `filterControlJournalPackets` drops the keys that left it.
 */
const decodeCache = new Map<string, DecodeFacts>();

function getDecodeFacts(packet: RawPacket): DecodeFacts {
  const key = getRawPacketObservationKey(packet);
  const cached = decodeCache.get(key);
  if (cached && cached.data === packet.data) return cached;
  let decoded: DecodedPacket | null;
  let typeName: string;
  try {
    decoded = MeshCoreDecoder.decode(packet.data);
    typeName = getDecodedPayloadTypeName(decoded);
  } catch {
    decoded = null;
    typeName = 'Unknown';
  }
  const facts = { data: packet.data, decoded, typeName };
  decodeCache.set(key, facts);
  return facts;
}

function journalTypeOf(packet: RawPacket, facts: DecodeFacts): ControlJournalPayloadType | null {
  return (
    canonicalControlJournalType(facts.typeName) ?? canonicalControlJournalType(packet.payload_type)
  );
}

export function filterControlJournalPackets(packets: RawPacket[]): RawPacket[] {
  const kept: RawPacket[] = [];
  const live = new Set<string>();
  for (const packet of packets) {
    live.add(getRawPacketObservationKey(packet));
    const facts = getDecodeFacts(packet);
    if (
      isControlJournalPayloadType(facts.typeName) ||
      isControlJournalPayloadType(packet.payload_type)
    ) {
      kept.push(packet);
    }
  }
  for (const key of decodeCache.keys()) {
    if (!live.has(key)) decodeCache.delete(key);
  }
  return kept;
}

function isRepeaterOrRoom(contact: Contact): boolean {
  return contact.type === CONTACT_TYPE_REPEATER || contact.type === CONTACT_TYPE_ROOM;
}

function findContactByKey(publicKey: string, contacts: Contact[]): Contact | undefined {
  const lower = publicKey.toLowerCase();
  return contacts.find((contact) => contact.public_key.toLowerCase() === lower);
}

function extractCorrelationTag(decoded: DecodedPacket | null): string | null {
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

export type ThreadKind = 'repeater' | 'channel' | 'us' | 'unresolved';

export interface JournalEntry {
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

export function classifyThread(
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

/** Request/response pairing by correlation tag. Returns new entries; never mutates its input. */
export function pairRequestsAndResponses(entries: JournalEntry[]): JournalEntry[] {
  const built = entries.map((entry) => ({ ...entry, pairedKey: null as string | null }));
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

interface EntryCacheSlot {
  packet: RawPacket;
  entry: JournalEntry;
}

/**
 * Entries built so far, valid for one (contacts, channels, publicKey) context.
 * A new context rebuilds them from the decode cache, never from the decoder.
 */
let entryContext: { contacts: Contact[]; channels: Channel[]; publicKey?: string } | null = null;
const entryCache = new Map<string, EntryCacheSlot>();

function buildEntry(
  packet: RawPacket,
  contacts: Contact[],
  channels: Channel[],
  keys: string[],
  publicKey?: string
): JournalEntry | null {
  const facts = getDecodeFacts(packet);
  const journalType = journalTypeOf(packet, facts);
  if (!journalType) return null;
  const decoded = facts.decoded;

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
  return { ...partial, pairedKey: null, ...thread };
}

/**
 * Incremental: an observation already built for the same packet and context is
 * reused, so only new observations are decoded and resolved. Pairing is
 * recomputed over the whole list each time.
 */
export function buildJournalEntries(
  packets: RawPacket[],
  contacts: Contact[],
  channels: Channel[],
  publicKey?: string
): JournalEntry[] {
  if (
    !entryContext ||
    entryContext.contacts !== contacts ||
    entryContext.channels !== channels ||
    entryContext.publicKey !== publicKey
  ) {
    entryCache.clear();
    entryContext = { contacts, channels, publicKey };
  }
  const keys = collectGroupDataKeys(channels);
  const built: JournalEntry[] = [];
  const seen = new Set<string>();

  for (const packet of packets) {
    const key = getRawPacketObservationKey(packet);
    seen.add(key);
    let slot = entryCache.get(key);
    if (!slot || slot.packet !== packet) {
      const entry = buildEntry(packet, contacts, channels, keys, publicKey);
      if (!entry) {
        entryCache.delete(key);
        continue;
      }
      slot = { packet, entry };
      entryCache.set(key, slot);
    }
    built.push(slot.entry);
  }
  for (const key of entryCache.keys()) {
    if (!seen.has(key)) entryCache.delete(key);
  }

  return pairRequestsAndResponses(built);
}

/** Test hook: forget every cached decode and entry. */
export function clearControlJournalCache(): void {
  decodeCache.clear();
  entryCache.clear();
  entryContext = null;
}

export function getControlJournalCacheSize(): { decoded: number; entries: number } {
  return { decoded: decodeCache.size, entries: entryCache.size };
}
