import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChannelForm, initialDraft } from '../components/settings/notifications/ChannelForm';
import { advancedBody, createBody } from '../components/settings/notifications/notificationsShared';
import i18n from '../i18n';
import type { NotificationChannel, NotificationTypeInfo } from '../notifications/types';

const limits = { per_min: null, per_hour: null, per_day: null };

function entry(overrides: Partial<NotificationTypeInfo> = {}): NotificationTypeInfo {
  return {
    type: 'email',
    schema: { properties: {} },
    cloud: false,
    singleton: false,
    preset: false,
    default_limits: limits,
    quiet_hours_exempt_default: false,
    include_message_text_default: 'always',
    templates: false,
    heartbeat: false,
    ...overrides,
  };
}

describe('channel digest mode', () => {
  it('is off by default and sent with the advanced settings', () => {
    const draft = initialDraft(entry());
    expect(draft.digest_interval_s).toBe(0);
    expect(advancedBody(draft).digest_interval_s).toBe(0);
    expect(createBody(entry(), draft, {}).digest_interval_s).toBe(0);
  });

  it('reads the saved interval', () => {
    const stored = {
      digest_interval_s: 900,
      config: {},
      secret_fields: [],
    } as unknown as NotificationChannel;
    expect(initialDraft(entry(), stored).digest_interval_s).toBe(900);
  });

  it('picks an interval in the advanced section, in English and French', async () => {
    await i18n.changeLanguage('en');
    render(<ChannelForm entry={entry()} onSubmit={vi.fn()} submitLabel="Save" idPrefix="d" />);
    expect(screen.getByTestId('channel-digest')).toHaveTextContent('Mentions, keyword alerts');
    const select = screen.getByLabelText('Digest mode');
    expect(select).toHaveValue('0');
    expect(screen.getByRole('option', { name: 'Every 15 minutes' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Every hour' })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: '3600' } });
    expect(select).toHaveValue('3600');

    await i18n.changeLanguage('fr');
    expect(screen.getByLabelText('Mode résumé')).toHaveValue('3600');
    expect(screen.getByRole('option', { name: 'Toutes les 15 minutes' })).toBeInTheDocument();
    await i18n.changeLanguage('en');
  });
});
