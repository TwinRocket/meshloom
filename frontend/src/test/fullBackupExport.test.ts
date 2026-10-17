import { describe, expect, it } from 'vitest';
import { fullBackupWarningKey } from '../components/settings/FullBackupExport';
import i18n from '../i18n';

describe('fullBackupWarningKey', () => {
  it('translates the section export warnings in both languages', async () => {
    for (const code of [
      'section_sensitive_unencrypted:accounts',
      'section_export_failed:plugins',
    ]) {
      const key = fullBackupWarningKey(code);
      expect(key, code).not.toBeNull();
      for (const language of ['en', 'fr']) {
        await i18n.changeLanguage(language);
        expect(i18n.t(key!), `${code} ${language}`).not.toBe(key);
      }
    }
    await i18n.changeLanguage('fr');
  });
});
