import { describe, expect, it } from 'vitest';

import type { Contact, DirectoryHopHit } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';
import {
  applyDirectoryName,
  chunkDirectoryPrefixes,
  collectDirectoryPrefixes,
  directoryPrefixesForLookup,
  isEligibleForDirectoryName,
  parseVisualizerNodeLookup,
} from '../utils/visualizerDirectoryNames';
import { getSceneNodeLabel } from '../components/visualizer/shared';

function createContact(publicKey: string, name: string, type = 1): Contact {
  return {
    public_key: publicKey,
    name,
    type,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    route_override_path: null,
    route_override_len: null,
    route_override_hash_mode: null,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

function hit(name: string, extras: Partial<DirectoryHopHit> = {}): DirectoryHopHit {
  return {
    name,
    source: 'corescope',
    hash_width: 2,
    ...extras,
  };
}

const hopNode = {
  id: '?aabb',
  type: 'repeater' as const,
  name: 'AABB',
  isAmbiguous: true,
  probableIdentity: null,
};

const pubkeyNode = {
  id: 'aabbccddeeff',
  type: 'client' as const,
  name: null,
  isAmbiguous: false,
  probableIdentity: null,
};

describe('parseVisualizerNodeLookup', () => {
  it('extracts hop tokens from ambiguous node ids', () => {
    expect(parseVisualizerNodeLookup('?aabb')).toEqual({ kind: 'hop', token: 'AABB' });
    expect(parseVisualizerNodeLookup('?aabb:>ccdd')).toEqual({ kind: 'hop', token: 'AABB' });
    expect(parseVisualizerNodeLookup('?aa')).toEqual({ kind: 'hop', token: 'AA' });
  });

  it('treats 12-hex ids as pubkey prefixes', () => {
    expect(parseVisualizerNodeLookup('aabbccddeeff')).toEqual({
      kind: 'pubkey',
      token: 'AABBCCDDEEFF',
    });
  });
});

describe('directoryPrefixesForLookup', () => {
  it('ignores 1-byte hops', () => {
    expect(directoryPrefixesForLookup({ kind: 'hop', token: 'AA' })).toEqual([]);
  });

  it('requests 2-byte then 3-byte prefixes for a 12-hex node', () => {
    expect(directoryPrefixesForLookup({ kind: 'pubkey', token: 'AABBCCDDEEFF' })).toEqual([
      'AABBCC',
      'AABB',
    ]);
  });
});

describe('isEligibleForDirectoryName', () => {
  it('skips 1-byte hops', () => {
    expect(isEligibleForDirectoryName({ ...hopNode, id: '?aa', name: 'AA' }, [])).toBe(false);
  });

  it('skips a unique local repeater', () => {
    expect(
      isEligibleForDirectoryName(hopNode, [
        createContact(
          'aabb00000000000000000000000000000000000000000000000000000000000000',
          'Hill',
          CONTACT_TYPE_REPEATER
        ),
      ])
    ).toBe(false);
  });

  it('skips locally ambiguous repeaters', () => {
    expect(
      isEligibleForDirectoryName(hopNode, [
        createContact(
          'aabb11111111111111111111111111111111111111111111111111111111111111',
          'A',
          CONTACT_TYPE_REPEATER
        ),
        createContact(
          'aabb22222222222222222222222222222222222222222222222222222222222222',
          'B',
          CONTACT_TYPE_REPEATER
        ),
      ])
    ).toBe(false);
  });

  it('skips advert-path probable identity', () => {
    expect(isEligibleForDirectoryName({ ...hopNode, probableIdentity: 'Hill' }, [])).toBe(false);
  });

  it('allows unknown 4-hex hops and unnamed pubkey nodes', () => {
    expect(isEligibleForDirectoryName(hopNode, [])).toBe(true);
    expect(isEligibleForDirectoryName(pubkeyNode, [])).toBe(true);
  });

  it('skips a pubkey node once a local contact name exists', () => {
    expect(
      isEligibleForDirectoryName(pubkeyNode, [
        createContact('aabbccddeeff0000000000000000000000000000000000000000000000000000', 'Alice'),
      ])
    ).toBe(false);
  });
});

describe('applyDirectoryName', () => {
  it('names a 4-hex hop from a corescope hit', () => {
    expect(applyDirectoryName(hopNode, { AABB: hit('RemoteHill') }, [])).toEqual({
      communityName: 'RemoteHill',
      nameSource: 'community',
    });
  });

  it('refuses a 12-hex node without a matching public_key', () => {
    expect(applyDirectoryName(pubkeyNode, { AABBCC: hit('Maybe') }, [])).toBeNull();
    expect(
      applyDirectoryName(
        pubkeyNode,
        {
          AABBCC: hit('Other', {
            public_key: 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
          }),
        },
        []
      )
    ).toBeNull();
  });

  it('names a 12-hex node when public_key matches', () => {
    expect(
      applyDirectoryName(
        pubkeyNode,
        {
          AABBCC: hit('Companion', {
            public_key: 'aabbccddeeff0000000000000000000000000000000000000000000000000000',
            hash_width: 3,
          }),
        },
        []
      )
    ).toEqual({
      communityName: 'Companion',
      nameSource: 'community',
    });
  });

  it('does not overlay a locally known node', () => {
    expect(
      applyDirectoryName(hopNode, { AABB: hit('RemoteHill') }, [
        createContact(
          'aabb00000000000000000000000000000000000000000000000000000000000000',
          'LocalHill',
          CONTACT_TYPE_REPEATER
        ),
      ])
    ).toBeNull();
  });
});

describe('collectDirectoryPrefixes', () => {
  it('collects only eligible unknown prefixes', () => {
    expect(
      collectDirectoryPrefixes(
        [
          hopNode,
          { ...hopNode, id: '?aa', name: 'AA' },
          { ...hopNode, id: 'self', type: 'self', name: 'Me', isAmbiguous: false },
        ],
        []
      )
    ).toEqual(['AABB']);
  });
});

describe('chunkDirectoryPrefixes', () => {
  it('splits into batches of 64', () => {
    const prefixes = Array.from({ length: 65 }, (_, index) =>
      index.toString(16).padStart(4, '0').toUpperCase()
    );
    const chunks = chunkDirectoryPrefixes(prefixes);
    expect(chunks).toHaveLength(2);
    expect(chunks[0]).toHaveLength(64);
    expect(chunks[1]).toHaveLength(1);
  });
});

describe('getSceneNodeLabel community overlay', () => {
  it('uses the Community name and drops the ambiguous suffix', () => {
    expect(
      getSceneNodeLabel({
        id: '?aabb',
        name: 'AABB',
        type: 'repeater',
        isAmbiguous: true,
        communityName: 'RemoteHill',
        nameSource: 'community',
      })
    ).toBe('RemoteHill');
  });
});
