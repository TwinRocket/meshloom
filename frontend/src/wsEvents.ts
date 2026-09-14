import type {
  Channel,
  CommunityLiveStatus,
  CommunityPacket,
  Contact,
  HealthStatus,
  Message,
  MessagePath,
  RawPacket,
} from './types';

export interface MessageAckedPayload {
  message_id: number;
  ack_count: number;
  paths?: MessagePath[];
  packet_id?: number | null;
}

export interface MessageDeletedPayload {
  message_id: number;
}

export interface ContactDeletedPayload {
  public_key: string;
}

export interface ContactResolvedPayload {
  previous_public_key: string;
  contact: Contact;
}

export interface ChannelDeletedPayload {
  key: string;
}

export interface ToastPayload {
  message: string;
  details?: string;
  code?: string;
  params?: Record<string, unknown>;
}

export type KnownWsEvent =
  | { type: 'health'; data: HealthStatus }
  | { type: 'message'; data: Message }
  | { type: 'contact'; data: Contact }
  | { type: 'contact_resolved'; data: ContactResolvedPayload }
  | { type: 'channel'; data: Channel }
  | { type: 'contact_deleted'; data: ContactDeletedPayload }
  | { type: 'channel_deleted'; data: ChannelDeletedPayload }
  | { type: 'raw_packet'; data: RawPacket }
  | { type: 'community_packet'; data: CommunityPacket }
  | { type: 'community_live'; data: CommunityLiveStatus }
  | { type: 'message_acked'; data: MessageAckedPayload }
  | { type: 'message_deleted'; data: MessageDeletedPayload }
  | { type: 'error'; data: ToastPayload }
  | { type: 'success'; data: ToastPayload }
  | { type: 'pong'; data?: null };

export interface UnknownWsEvent {
  type: 'unknown';
  rawType: string;
  data: unknown;
}

export type ParsedWsEvent = KnownWsEvent | UnknownWsEvent;

interface RawWsEnvelope {
  type?: unknown;
  data?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** True when immediately-dereferenced payload fields are present. Extra fields are allowed. */
export function isDispatchableWsEvent(event: ParsedWsEvent): boolean {
  if (event.type === 'unknown' || event.type === 'pong') {
    return true;
  }
  const data = event.data;
  switch (event.type) {
    case 'message':
      return isRecord(data);
    case 'contact':
      return isRecord(data) && typeof data.public_key === 'string';
    case 'contact_resolved':
      return (
        isRecord(data) && typeof data.previous_public_key === 'string' && isRecord(data.contact)
      );
    case 'contact_deleted':
      return isRecord(data) && typeof data.public_key === 'string';
    case 'channel_deleted':
      return isRecord(data) && typeof data.key === 'string';
    case 'message_acked':
      return (
        isRecord(data) && typeof data.message_id === 'number' && typeof data.ack_count === 'number'
      );
    case 'message_deleted':
      return isRecord(data) && typeof data.message_id === 'number';
    case 'community_packet':
    case 'community_live':
      return isRecord(data);
    case 'error':
    case 'success':
      return isRecord(data) && typeof data.message === 'string';
    default:
      return true;
  }
}

export function parseWsEvent(raw: string): ParsedWsEvent {
  const parsed: RawWsEnvelope = JSON.parse(raw);
  if (!parsed || typeof parsed !== 'object' || typeof parsed.type !== 'string') {
    throw new Error('Invalid WebSocket event envelope');
  }

  switch (parsed.type) {
    case 'health':
    case 'message':
    case 'contact':
    case 'contact_resolved':
    case 'channel':
    case 'contact_deleted':
    case 'channel_deleted':
    case 'raw_packet':
    case 'community_packet':
    case 'community_live':
    case 'message_acked':
    case 'message_deleted':
    case 'error':
    case 'success':
      return {
        type: parsed.type,
        data: parsed.data,
      } as KnownWsEvent;
    case 'pong':
      return { type: 'pong', data: parsed.data as null | undefined };
    default:
      return {
        type: 'unknown',
        rawType: parsed.type,
        data: parsed.data,
      };
  }
}
