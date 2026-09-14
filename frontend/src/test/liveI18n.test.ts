import { describe, expect, it } from 'vitest';

import i18n from '../i18n';

const LIVE_KEYS = [
  'live.bannerInactive',
  'live.bannerOptOut',
  'live.iataFilter',
  'live.iataAll',
  'live.typeFilter',
  'live.certainOnly',
  'live.certainOnlyHelp',
  'live.play',
  'live.pause',
  'live.playPause',
  'live.mapAria',
  'live.legend.advert',
  'live.legend.text',
  'live.legend.ack',
  'live.legend.trace',
  'live.legend.other',
  'live.confidence.exact',
  'live.confidence.probable',
  'live.reason.ambiguous_prefix',
  'live.reason.no_candidate',
  'live.reason.no_position',
  'live.reason.geo_filtered',
  'live.reason.skipped_unresolved',
  'live.nodes.repeater',
  'live.nodes.room',
  'live.nodes.client',
  'live.nodes.sensor',
  'live.nodes.unknown',
  'live.ears.advert',
  'live.ears.iata',
  'live.ears.local',
] as const;

describe('live i18n', () => {
  it('keeps French and English copy for every live key', () => {
    for (const key of LIVE_KEYS) {
      expect(i18n.getFixedT('fr')(key)).not.toBe(key);
      expect(i18n.getFixedT('en')(key)).not.toBe(key);
    }
  });

  it('does not keep the retired slot-busy or Relancer copy', () => {
    expect(i18n.exists('live.bannerSlotBusy')).toBe(false);
    expect(i18n.exists('live.relancer')).toBe(false);
    expect(i18n.exists('live.bannerExpired')).toBe(false);
    expect(i18n.exists('live.bannerRateLimit')).toBe(false);
  });
});
