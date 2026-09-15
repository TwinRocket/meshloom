import type { CommunityPacket, DirectoryMapNode } from '../types';

/** Nice Côte d'Azur airport centroid from `iataCentroids.json`. */
export const NCE_IATA_CENTROID = { lat: 43.6584, lon: 7.2159 };

export const NCE_MONT_CAUME = {
  name: 'FR83-Mont-Caume',
  pubkey: 'f60431bc302cb60259562ca4e39973a347986bb44925c7a677e18814a77ff407',
  lat: 43.182626,
  lon: 5.898634,
} as const;

/** Captured community_packet that rendered FR83-Mont-Caume on the Nice coast. */
export const NCE_MONT_CAUME_FRAME: CommunityPacket = {
  v: 2,
  event_id: 'e3ad43e0c2ae49969048e1ec0773d72a',
  hash8: '6e6939eb',
  type: 'other',
  iata: 'NCE',
  snr: -9,
  t: 1789360773578,
  hop_count: 6,
  ear: { lat: 43.760531, lon: 7.177876, source: 'advert' },
  ear_id: '71c397fdd1390ba98b555aeb2aea812cdc164e1a5da51aa731d2c89e335e6f03',
  path: ['7f91', 'a3cb', 'f5e6', '3b42', 'f604', '56d9'],
  hops: [
    {
      token: '7f91',
      confidence: 'unresolved',
      reason: 'geo_filtered',
    },
    {
      token: 'a3cb',
      confidence: 'exact',
      lat: 43.600193,
      lon: 3.825857,
      name: 'FR34MPL-MAR',
      pubkey: 'a3cb5563fe76597bf657d29109126b5120e477b3b0c3c914079fc575aeeaf588',
    },
    {
      token: 'f5e6',
      confidence: 'exact',
      lat: 43.535191,
      lon: 3.811703,
      name: 'FR34MPL-VLM',
      pubkey: 'f5e6b76f5a3ef9d1db5f4dbdff2edd654c0110a527aca955129854d404a371b5',
    },
    {
      token: '3b42',
      confidence: 'exact',
      lat: 43.20536,
      lon: 5.953823,
      name: 'FR83-Grand-Cap',
      pubkey: '3b42e2faf534e33fb390b9c7d51c0f9ea01487b2dfa2f26a6a479c582f4d0973',
    },
    {
      token: 'f604',
      confidence: 'exact',
      lat: NCE_MONT_CAUME.lat,
      lon: NCE_MONT_CAUME.lon,
      name: NCE_MONT_CAUME.name,
      pubkey: NCE_MONT_CAUME.pubkey,
    },
    {
      token: '56d9',
      confidence: 'exact',
      lat: 43.800231,
      lon: 7.412203,
      name: 'FR06-PEIL-RPL1\u2600\ufe0f',
      pubkey: '56d9854b379d33d1f7d94681e3c684661d3bab50c37b40be9fcc3fa7127cd906',
    },
  ],
};

export const NCE_LIVE_ROLE_NODES: DirectoryMapNode[] = [
  {
    public_key: NCE_MONT_CAUME.pubkey,
    name: NCE_MONT_CAUME.name,
    role: 'repeater',
    lat: NCE_MONT_CAUME.lat,
    lon: NCE_MONT_CAUME.lon,
    source: 'community-db',
    last_seen: 1_700_000_000,
  },
  {
    public_key: `${'c0'.repeat(32)}`,
    name: 'NCE-Companion',
    role: 'companion',
    lat: 43.703,
    lon: 7.266,
    source: 'local',
    last_seen: 1_700_000_100,
  },
  {
    public_key: `${'r0'.repeat(32)}`,
    name: 'NCE-Room',
    role: 'room',
    lat: 43.71,
    lon: 7.28,
    source: 'community-db',
    last_seen: 1_700_000_200,
  },
];

export const NCE_ADVERT_FRAME: CommunityPacket = {
  v: 2,
  event_id: 'nce-advert-mont-caume',
  hash8: 'aabbccdd',
  type: 'advert',
  iata: 'NCE',
  snr: -3,
  t: Date.now(),
  hop_count: 1,
  path: ['f604'],
  hops: [
    {
      token: 'f604',
      confidence: 'exact',
      lat: NCE_MONT_CAUME.lat,
      lon: NCE_MONT_CAUME.lon,
      name: NCE_MONT_CAUME.name,
      pubkey: NCE_MONT_CAUME.pubkey,
    },
  ],
  ear: { lat: 43.760531, lon: 7.177876, source: 'advert' },
  ear_id: '71c397fdd1390ba98b555aeb2aea812cdc164e1a5da51aa731d2c89e335e6f03',
};

export const NCE_IATA_EAR_FRAME: CommunityPacket = {
  v: 2,
  event_id: 'nce-iata-ear',
  hash8: '11223344',
  type: 'text',
  iata: 'NCE',
  snr: 2,
  t: Date.now(),
  hop_count: 1,
  path: ['f604'],
  hops: [
    {
      token: 'f604',
      confidence: 'exact',
      lat: NCE_MONT_CAUME.lat,
      lon: NCE_MONT_CAUME.lon,
      name: NCE_MONT_CAUME.name,
      pubkey: NCE_MONT_CAUME.pubkey,
    },
  ],
  ear: { lat: NCE_IATA_CENTROID.lat, lon: NCE_IATA_CENTROID.lon, source: 'iata' },
  ear_id: 'ear-nce-iata',
};
