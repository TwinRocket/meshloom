import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { PermissionList } from '../components/settings/extensions/PermissionList';
import i18n from '../i18n';

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

describe('notifications.send consent (MSHL-213)', () => {
  it('names the rules matrix as the destination and shows the default quota', () => {
    render(<PermissionList declared={{ 'notifications.send': {} }} />);
    expect(screen.getByText(t('plugins.permissions.notifications_send.title'))).toBeInTheDocument();
    expect(
      screen.getByText(t('plugins.permissions.notifications_send.description'))
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        t('plugins.permissions.notifications_send.quota', { per_hour: 10, per_day: 50 })
      )
    ).toBeInTheDocument();
  });

  it('shows the quota the manifest asks for', () => {
    render(<PermissionList declared={{ 'notifications.send': { per_hour: 3, per_day: 7 } }} />);
    expect(
      screen.getByText(
        t('plugins.permissions.notifications_send.quota', { per_hour: 3, per_day: 7 })
      )
    ).toBeInTheDocument();
  });

  it('labels the event in the rules matrix and the delivery log, in both languages', () => {
    for (const lng of ['en', 'fr']) {
      const label = i18n.t('settings.notifications.matrix.events.plugin_notification.label', {
        lng,
      });
      const log = i18n.t('settings.notifications.log.events.plugin_notification', { lng });
      expect(label).not.toContain('plugin_notification');
      expect(log).not.toContain('plugin_notification');
    }
  });
});
