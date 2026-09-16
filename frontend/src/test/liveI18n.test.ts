import { describe, expect, it } from 'vitest';

import i18n from '../i18n';

const LIVE_KEYS = [
  'live.bannerOptOut',
  'live.iataFilter',
  'live.iataAll',
  'live.typeFilter',
  'live.certainOnly',
  'live.certainOnlyHelp',
  'live.mapAria',
  'live.legendTitle',
  'live.packetLegend',
  'live.roleLegend',
  'live.legend.req',
  'live.legend.response',
  'live.legend.text',
  'live.legend.ack',
  'live.legend.advert',
  'live.legend.grp_txt',
  'live.legend.grp_data',
  'live.legend.anon_req',
  'live.legend.path',
  'live.legend.trace',
  'live.legend.multipart',
  'live.legend.control',
  'live.legend.raw_custom',
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
  'live.nodes.companion',
  'live.nodes.sensor',
  'live.nodes.unknown',
  'live.localRadio',
] as const;

describe('live i18n', () => {
  it('keeps French and English copy for every live key', () => {
    for (const key of LIVE_KEYS) {
      expect(i18n.getFixedT('fr')(key)).not.toBe(key);
      expect(i18n.getFixedT('en')(key)).not.toBe(key);
    }
  });

  it('does not keep the retired slot-busy, Relancer, play/pause, or 24h copy', () => {
    expect(i18n.exists('live.bannerSlotBusy')).toBe(false);
    expect(i18n.exists('live.relancer')).toBe(false);
    expect(i18n.exists('live.bannerExpired')).toBe(false);
    expect(i18n.exists('live.bannerRateLimit')).toBe(false);
    expect(i18n.exists('live.bannerInactive')).toBe(false);
    expect(i18n.exists('live.play')).toBe(false);
    expect(i18n.exists('live.pause')).toBe(false);
    expect(i18n.exists('live.playPause')).toBe(false);
  });

  it('drops the observer role and the community ear labels', () => {
    expect(i18n.exists('live.nodes.observer')).toBe(false);
    expect(i18n.exists('live.ears.advert')).toBe(false);
    expect(i18n.exists('live.ears.iata')).toBe(false);
  });

  it('never calls the live feed rain', () => {
    for (const lng of ['en', 'fr'] as const) {
      const t = i18n.getFixedT(lng);
      for (const key of LIVE_KEYS) {
        expect(t(key).toLowerCase()).not.toContain('rain');
        expect(t(key).toLowerCase()).not.toContain('pluie');
      }
      const sidebarLive = t('sidebar.live').toLowerCase();
      expect(sidebarLive).not.toContain('rain');
      expect(sidebarLive).not.toContain('pluie');
      expect(t('live.bannerOptOut').toLowerCase()).not.toContain('24');
    }
  });

  it('labels the companion role Companion in English and French', () => {
    expect(i18n.getFixedT('en')('live.nodes.companion')).toBe('Companion');
    expect(i18n.getFixedT('en')('live.nodes.client')).toBe('Companion');
    expect(i18n.getFixedT('fr')('live.nodes.companion')).toBe('Companion');
    expect(i18n.getFixedT('fr')('live.nodes.client')).toBe('Companion');
  });
});
