import { getChannelHash, verifyMac } from 'meshcore-hashtag-cracker';

import { extractPacketPayloadHex } from './pathUtils';

export interface GroupDataPlaintext {
  data_type: number;
  data_len: number;
  data_hex: string;
  data_text: string | null;
}

const GROUP_DATA_PAYLOAD_TYPE = 6;

const SBOX = new Uint8Array([
  0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
  0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
  0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
  0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
  0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
  0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
  0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
  0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
  0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
  0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
  0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
  0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
  0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
  0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
  0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
  0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]);

const INV_SBOX = new Uint8Array(256);
for (let i = 0; i < 256; i += 1) {
  INV_SBOX[SBOX[i]] = i;
}

const RCON = new Uint8Array([0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]);

function xtime(value: number): number {
  return ((value << 1) ^ (value & 0x80 ? 0x1b : 0)) & 0xff;
}

function multiply(a: number, b: number): number {
  let result = 0;
  let aa = a;
  let bb = b;
  while (bb) {
    if (bb & 1) result ^= aa;
    aa = xtime(aa);
    bb >>= 1;
  }
  return result;
}

function hexToBytes(hex: string): Uint8Array | null {
  const normalized = hex
    .trim()
    .replace(/^0x/i, '')
    .replace(/[\s:]+/g, '');
  if (
    normalized.length === 0 ||
    normalized.length % 2 !== 0 ||
    !/^[0-9a-fA-F]+$/.test(normalized)
  ) {
    return null;
  }
  const bytes = new Uint8Array(normalized.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = Number.parseInt(normalized.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function asBytes(input: Uint8Array | string): Uint8Array | null {
  if (typeof input === 'string') {
    return hexToBytes(input);
  }
  return input;
}

function expandKey(key: Uint8Array): Uint32Array {
  const w = new Uint32Array(44);
  for (let i = 0; i < 4; i += 1) {
    w[i] = (key[i * 4] << 24) | (key[i * 4 + 1] << 16) | (key[i * 4 + 2] << 8) | key[i * 4 + 3];
  }
  for (let i = 4; i < 44; i += 1) {
    let temp = w[i - 1];
    if (i % 4 === 0) {
      const rotated = ((temp << 8) | (temp >>> 24)) >>> 0;
      const sub =
        (SBOX[(rotated >>> 24) & 0xff] << 24) |
        (SBOX[(rotated >>> 16) & 0xff] << 16) |
        (SBOX[(rotated >>> 8) & 0xff] << 8) |
        SBOX[rotated & 0xff];
      temp = (sub ^ (RCON[i / 4] << 24)) >>> 0;
    }
    w[i] = (w[i - 4] ^ temp) >>> 0;
  }
  return w;
}

function addRoundKey(state: Uint8Array, w: Uint32Array, round: number): void {
  for (let c = 0; c < 4; c += 1) {
    const word = w[round * 4 + c];
    state[c * 4] ^= (word >>> 24) & 0xff;
    state[c * 4 + 1] ^= (word >>> 16) & 0xff;
    state[c * 4 + 2] ^= (word >>> 8) & 0xff;
    state[c * 4 + 3] ^= word & 0xff;
  }
}

function invShiftRows(state: Uint8Array): void {
  const t = state.slice();
  state[1] = t[13];
  state[5] = t[1];
  state[9] = t[5];
  state[13] = t[9];
  state[2] = t[10];
  state[6] = t[14];
  state[10] = t[2];
  state[14] = t[6];
  state[3] = t[7];
  state[7] = t[11];
  state[11] = t[15];
  state[15] = t[3];
}

function invSubBytes(state: Uint8Array): void {
  for (let i = 0; i < 16; i += 1) {
    state[i] = INV_SBOX[state[i]];
  }
}

function invMixColumns(state: Uint8Array): void {
  for (let c = 0; c < 4; c += 1) {
    const i = c * 4;
    const a0 = state[i];
    const a1 = state[i + 1];
    const a2 = state[i + 2];
    const a3 = state[i + 3];
    state[i] = multiply(a0, 0x0e) ^ multiply(a1, 0x0b) ^ multiply(a2, 0x0d) ^ multiply(a3, 0x09);
    state[i + 1] =
      multiply(a0, 0x09) ^ multiply(a1, 0x0e) ^ multiply(a2, 0x0b) ^ multiply(a3, 0x0d);
    state[i + 2] =
      multiply(a0, 0x0d) ^ multiply(a1, 0x09) ^ multiply(a2, 0x0e) ^ multiply(a3, 0x0b);
    state[i + 3] =
      multiply(a0, 0x0b) ^ multiply(a1, 0x0d) ^ multiply(a2, 0x09) ^ multiply(a3, 0x0e);
  }
}

function decryptBlock(block: Uint8Array, w: Uint32Array): Uint8Array {
  const state = block.slice();
  addRoundKey(state, w, 10);
  for (let round = 9; round >= 1; round -= 1) {
    invShiftRows(state);
    invSubBytes(state);
    addRoundKey(state, w, round);
    invMixColumns(state);
  }
  invShiftRows(state);
  invSubBytes(state);
  addRoundKey(state, w, 0);
  return state;
}

function aes128EcbDecrypt(key: Uint8Array, ciphertext: Uint8Array): Uint8Array {
  const w = expandKey(key);
  const plaintext = new Uint8Array(ciphertext.length);
  for (let offset = 0; offset < ciphertext.length; offset += 16) {
    plaintext.set(decryptBlock(ciphertext.subarray(offset, offset + 16), w), offset);
  }
  return plaintext;
}

function normalizeChannelKey(key: string): string | null {
  const normalized = key
    .trim()
    .toLowerCase()
    .replace(/^0x/, '')
    .replace(/[\s:]+/g, '');
  if (normalized.length !== 32 || !/^[0-9a-f]+$/.test(normalized)) {
    return null;
  }
  return normalized;
}

function parseGroupDataPlaintext(plaintext: Uint8Array): GroupDataPlaintext | null {
  if (plaintext.length < 3) {
    return null;
  }
  const dataType = plaintext[0] | (plaintext[1] << 8);
  const dataLen = plaintext[2];
  if (plaintext.length < 3 + dataLen) {
    return null;
  }
  const data = plaintext.subarray(3, 3 + dataLen);
  let dataText: string | null = null;
  try {
    dataText = new TextDecoder('utf-8', { fatal: true }).decode(data);
  } catch {
    dataText = null;
  }
  return {
    data_type: dataType,
    data_len: dataLen,
    data_hex: bytesToHex(data),
    data_text: dataText,
  };
}

function decryptEnvelope(payload: Uint8Array, keys: string[]): GroupDataPlaintext | null {
  if (payload.length < 3) {
    return null;
  }
  const hashHex = payload[0].toString(16).padStart(2, '0');
  const macHex = bytesToHex(payload.subarray(1, 3));
  const ciphertext = payload.subarray(3);
  if (ciphertext.length === 0 || ciphertext.length % 16 !== 0) {
    return null;
  }
  const ciphertextHex = bytesToHex(ciphertext);

  for (const key of keys) {
    const normalized = normalizeChannelKey(key);
    if (!normalized) {
      continue;
    }
    if (getChannelHash(normalized) !== hashHex) {
      continue;
    }
    if (!verifyMac(ciphertextHex, macHex, normalized)) {
      continue;
    }
    const keyBytes = hexToBytes(normalized);
    if (!keyBytes) {
      continue;
    }
    const parsed = parseGroupDataPlaintext(aes128EcbDecrypt(keyBytes, ciphertext));
    if (parsed) {
      return parsed;
    }
  }
  return null;
}

function extractGroupDataPayload(bytes: Uint8Array): Uint8Array | 'skip' | null {
  if (bytes.length < 2) {
    return null;
  }
  const header = bytes[0];
  if (((header >> 2) & 0x0f) !== GROUP_DATA_PAYLOAD_TYPE) {
    return null;
  }
  if (((header >> 6) & 0x03) !== 0) {
    return 'skip';
  }
  const payloadHex = extractPacketPayloadHex(bytesToHex(bytes));
  if (!payloadHex) {
    return null;
  }
  const payload = hexToBytes(payloadHex);
  if (!payload || payload.length < 3 || (payload.length - 3) % 16 !== 0) {
    return null;
  }
  return payload;
}

function isNonGroupDataMeshPacket(bytes: Uint8Array): boolean {
  if (bytes.length < 2) {
    return false;
  }
  const payloadType = (bytes[0] >> 2) & 0x0f;
  if (payloadType === GROUP_DATA_PAYLOAD_TYPE) {
    return false;
  }
  const payloadHex = extractPacketPayloadHex(bytesToHex(bytes));
  if (!payloadHex) {
    return false;
  }
  const payload = hexToBytes(payloadHex);
  if (!payload) {
    return false;
  }
  return payload.length <= bytes.length - 2;
}

/**
 * Decrypt a GroupData envelope (hash + MAC + ciphertext) or a full v0 packet.
 * Tries keys whose SHA256[0] matches the hash byte; first MAC OK wins.
 * Full MeshCore packets that are not GroupData are rejected (no envelope fallback).
 */
export function parseGroupData(
  payloadOrPacket: Uint8Array | string,
  keys: string[]
): GroupDataPlaintext | null {
  const bytes = asBytes(payloadOrPacket);
  if (!bytes || keys.length === 0) {
    return null;
  }
  const extracted = extractGroupDataPayload(bytes);
  if (extracted === 'skip') {
    return null;
  }
  if (extracted) {
    return decryptEnvelope(extracted, keys);
  }
  if (isNonGroupDataMeshPacket(bytes)) {
    return null;
  }
  return decryptEnvelope(bytes, keys);
}
