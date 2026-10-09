import { afterEach, describe, expect, it } from 'vitest';

import i18n from '../i18n';
import { formatNumber } from '../utils/formatNumber';

describe('formatNumber', () => {
  const original = i18n.language;
  afterEach(async () => {
    await i18n.changeLanguage(original);
  });

  it('follows the UI language, not the host locale', async () => {
    await i18n.changeLanguage('en');
    expect(formatNumber(1234567.5)).toBe('1,234,567.5');
    await i18n.changeLanguage('fr');
    expect(formatNumber(1234567.5)).toBe(new Intl.NumberFormat('fr').format(1234567.5));
    expect(formatNumber(1234567.5)).not.toContain(',567');
  });

  it('formats {{count, number}} interpolation with the active language', async () => {
    await i18n.changeLanguage('fr');
    expect(i18n.t('rawPacket.packetsCount', { count: 12345 })).toBe(
      `${new Intl.NumberFormat('fr').format(12345)} paquets`
    );
  });
});
