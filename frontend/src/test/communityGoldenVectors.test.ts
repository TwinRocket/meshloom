/**
 * Shared golden vectors for the hashtag key and channel-hash byte (issue #51).
 *
 * Provenance: tests/fixtures/community_golden_vectors.json at the repo root is a
 * byte-for-byte copy of TwinRocket/meshloom-community
 * docs/contracts/vectors/golden.json (branch fix/L10-contracts, vectors version 1),
 * sha256 8e777e64aee4e5e250057419c0c4184537d04321de7d1b0e4408a90521279cbb.
 * Never edit the copy to make a test pass: a failure is a divergence to report.
 *
 * The frontend has no packet-hash implementation (it shows the backend's
 * `packet_hash`), so only the hashtag family applies here.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { deriveChannelHashByte, deriveHashtagKeyHex } from '../utils/hashtagKey';

const VECTORS_PATH = join(
  __dirname,
  '..',
  '..',
  '..',
  'tests',
  'fixtures',
  'community_golden_vectors.json'
);
const VECTORS_SHA256 = '8e777e64aee4e5e250057419c0c4184537d04321de7d1b0e4408a90521279cbb';

interface HashtagCase {
  id: string;
  input: string;
  hashed_text: string;
  key_hex: string;
  hash_byte: string;
}

const raw = readFileSync(VECTORS_PATH);
const vectors = JSON.parse(raw.toString('utf8')) as {
  version: number;
  hashtag: { cases: HashtagCase[] };
};

describe('community golden vectors', () => {
  it('is an unedited copy', () => {
    expect(createHash('sha256').update(raw).digest('hex')).toBe(VECTORS_SHA256);
    expect(vectors.version).toBe(1);
  });

  it.each(vectors.hashtag.cases.map((c) => [c.id, c] as const))('hashtag %s', (_id, c) => {
    const key = deriveHashtagKeyHex(c.input);
    expect(key).toBe(c.key_hex);
    expect(deriveChannelHashByte(key)).toBe(c.hash_byte);
  });
});
