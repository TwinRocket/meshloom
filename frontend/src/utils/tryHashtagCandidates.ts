import { ChannelCrypto } from '@michaelhart/meshcore-decoder';
import { isTimestampValid, isValidUtf8, verifyMac } from 'meshcore-hashtag-cracker';

import { deriveChannelHashByte, deriveHashtagKeyHex, normalizeHashtagName } from './hashtagKey';
import { extractPacketPayloadHex } from './pathUtils';

export interface GroupTextFields {
  channelHash: string;
  cipherMac: string;
  ciphertext: string;
}

export interface TryHashtagOptions {
  nowSec?: number;
  validSeconds?: number;
  useTimestampFilter?: boolean;
  useSenderFilter?: boolean;
  useUtf8Filter?: boolean;
}

export interface HashtagCandidateMatch {
  ok: true;
  key: string;
  roomName: string;
  message: string;
}

const VALID_ROOM_NAME = /^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$|^[a-z0-9]$/;

function isValidDictionaryName(name: string): boolean {
  return VALID_ROOM_NAME.test(name);
}

export function extractGroupTextFields(packetHex: string): GroupTextFields | null {
  const cleanHex = packetHex.trim().replace(/\s+/g, '').replace(/^0x/i, '');
  if (cleanHex.length % 2 !== 0 || cleanHex.length < 2 || !/^[0-9a-f]+$/i.test(cleanHex)) {
    return null;
  }

  const header = Number.parseInt(cleanHex.slice(0, 2), 16);
  if (((header >> 2) & 0x0f) !== 0x05) {
    return null;
  }

  const payload = extractPacketPayloadHex(cleanHex);
  if (!payload || payload.length < 6) {
    return null;
  }

  const ciphertext = payload.slice(6).toLowerCase();
  if (ciphertext.length === 0 || ciphertext.length % 32 !== 0) {
    return null;
  }

  return {
    channelHash: payload.slice(0, 2).toLowerCase(),
    cipherMac: payload.slice(2, 6).toLowerCase(),
    ciphertext,
  };
}

export function tryHashtagName(
  packetHex: string,
  name: string,
  opts: TryHashtagOptions = {}
): HashtagCandidateMatch | null {
  const fields = extractGroupTextFields(packetHex);
  if (!fields) {
    return null;
  }

  const roomName = normalizeHashtagName(name);
  // Exact name: re-add the '#' so a name like "##x" is not stripped twice.
  const key = deriveHashtagKeyHex(`#${roomName}`);
  if (
    deriveChannelHashByte(key) !== fields.channelHash ||
    !verifyMac(fields.ciphertext, fields.cipherMac, key)
  ) {
    return null;
  }

  const decrypted = ChannelCrypto.decryptGroupTextMessage(fields.ciphertext, fields.cipherMac, key);
  if (!decrypted.success || !decrypted.data) {
    return null;
  }

  const {
    nowSec,
    validSeconds,
    useTimestampFilter = true,
    useSenderFilter = true,
    useUtf8Filter = true,
  } = opts;
  const { timestamp, sender, message } = decrypted.data;
  if (
    (useTimestampFilter && !isTimestampValid(timestamp, validSeconds, nowSec)) ||
    (useUtf8Filter && !isValidUtf8(message)) ||
    (useSenderFilter && !sender)
  ) {
    return null;
  }

  return {
    ok: true,
    key,
    roomName,
    message: sender ? `${sender}: ${message}` : message,
  };
}

export function tryHashtagCandidates(
  packetHex: string,
  names: readonly string[],
  opts: TryHashtagOptions = {}
): HashtagCandidateMatch | null {
  for (const name of names) {
    const match = tryHashtagName(packetHex, name, opts);
    if (match) {
      return match;
    }
  }
  return null;
}

export function partitionWordlistNames(names: readonly string[]): {
  verbatim: string[];
  dictionary: string[];
} {
  const verbatim: string[] = [];
  const dictionary: string[] = [];

  for (const name of names) {
    const normalized = normalizeHashtagName(name);
    (isValidDictionaryName(normalized) ? dictionary : verbatim).push(normalized);
  }

  return { verbatim, dictionary };
}

export function mergePriorityWordlist(
  priority: readonly string[],
  english: readonly string[]
): string[] {
  const merged: string[] = [];
  const seen = new Set<string>();

  for (const name of [...priority, ...english]) {
    const normalized = normalizeHashtagName(name).trim().toLowerCase();
    if (isValidDictionaryName(normalized) && !seen.has(normalized)) {
      seen.add(normalized);
      merged.push(normalized);
    }
  }

  return merged;
}
