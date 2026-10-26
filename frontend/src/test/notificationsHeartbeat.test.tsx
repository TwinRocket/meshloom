import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import { ChannelForm, initialDraft } from '../components/settings/notifications/ChannelForm';
import {
  createBody,
  heartbeatBody,
  SECRET_MASK,
} from '../components/settings/notifications/notificationsShared';
import i18n from '../i18n';
import type { NotificationChannel, NotificationTypeInfo } from '../notifications/types';

const limits = { per_min: null, per_hour: null, per_day: null };

function entry(overrides: Partial<NotificationTypeInfo>): NotificationTypeInfo {
  return {
    type: 'webhook',
    schema: { properties: {} },
    cloud: false,
    singleton: false,
    preset: false,
    default_limits: limits,
    quiet_hours_exempt_default: false,
    include_message_text_default: 'always',
    templates: false,
    heartbeat: true,
    ...overrides,
  };
}

function channel(overrides: Partial<NotificationChannel>): NotificationChannel {
  return {
    id: 1,
    created_at: '2026-10-26T00:00:00Z',
    config: {},
    secret_fields: [],
    ...overrides,
  } as unknown as NotificationChannel;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('channel heartbeat', () => {
  it('is off by default and sends no URL', () => {
    const draft = initialDraft(entry({}));
    expect(draft.heartbeat_interval_s).toBe(0);
    expect(heartbeatBody(entry({}), draft)).toEqual({
      heartbeat_interval_s: 0,
      heartbeat_method: 'GET',
    });
  });

  it('picks an interval, then shows the ping URL and method', async () => {
    await i18n.changeLanguage('en');
    render(<ChannelForm entry={entry({})} onSubmit={vi.fn()} submitLabel="Save" idPrefix="h" />);
    expect(screen.getByTestId('channel-heartbeat')).toHaveTextContent('Home Assistant');
    expect(screen.queryByLabelText('Ping URL (optional)')).toBeNull();

    fireEvent.change(screen.getByLabelText('Heartbeat'), { target: { value: '900' } });
    expect(
      within(screen.getByTestId('channel-heartbeat')).getByRole('option', {
        name: 'Every 15 minutes',
      })
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId('channel-heartbeat')).getByRole('option', { name: 'Every 6 hours' })
    ).toBeInTheDocument();
    const url = screen.getByLabelText('Ping URL (optional)');
    expect(url).toHaveAttribute('placeholder', 'https://hc-ping.com/…');
    expect(screen.getByLabelText('Method')).toHaveValue('GET');
    expect(screen.queryByLabelText('Remove the saved ping URL')).toBeNull();
  });

  it('keeps, replaces or clears a saved URL without showing it', async () => {
    await i18n.changeLanguage('en');
    const stored = channel({
      heartbeat_interval_s: 3600,
      heartbeat_method: 'POST',
      heartbeat_url: SECRET_MASK,
      secret_fields: ['heartbeat_url'],
    });
    const kind = entry({});
    const draft = initialDraft(kind, stored);
    expect(draft).toMatchObject({ heartbeat_interval_s: 3600, heartbeat_method: 'POST' });
    expect(draft.heartbeat_url).toBe('');
    expect(heartbeatBody(kind, draft)).not.toHaveProperty('heartbeat_url');
    expect(
      heartbeatBody(kind, { ...draft, heartbeat_url: ' https://hc-ping.com/x ' })
    ).toMatchObject({ heartbeat_url: 'https://hc-ping.com/x' });
    expect(heartbeatBody(kind, { ...draft, heartbeat_url_clear: true })).toMatchObject({
      heartbeat_url: '',
    });

    render(
      <ChannelForm
        entry={kind}
        channel={stored}
        onSubmit={vi.fn()}
        submitLabel="Save"
        idPrefix="h"
      />
    );
    const url = screen.getByLabelText('Ping URL (optional)');
    expect(url).toHaveValue('');
    expect(url).toHaveAttribute('placeholder', 'Saved: leave empty to keep it');
    fireEvent.click(screen.getByLabelText('Remove the saved ping URL'));
    expect(url).toBeDisabled();
  });

  it('offers a stored custom interval', () => {
    render(
      <ChannelForm
        entry={entry({})}
        channel={channel({ heartbeat_interval_s: 1200 })}
        onSubmit={vi.fn()}
        submitLabel="Save"
        idPrefix="h"
      />
    );
    expect(screen.getByLabelText('Heartbeat')).toHaveValue('1200');
  });

  it('is absent on a type without heartbeat and stays out of its body', () => {
    vi.spyOn(api, 'previewNotificationTemplate').mockResolvedValue({
      dm: '',
      channel: '',
      variables: [],
    });
    const email = entry({ type: 'email', heartbeat: false });
    render(<ChannelForm entry={email} onSubmit={vi.fn()} submitLabel="Save" idPrefix="h" />);
    expect(screen.queryByTestId('channel-heartbeat')).toBeNull();
    expect(createBody(email, initialDraft(email), {})).not.toHaveProperty('heartbeat_interval_s');
  });

  it('is in French too', async () => {
    await i18n.changeLanguage('fr');
    render(<ChannelForm entry={entry({})} onSubmit={vi.fn()} submitLabel="Save" idPrefix="h" />);
    expect(screen.getByLabelText('Battement de cœur')).toBeInTheDocument();
    expect(
      within(screen.getByTestId('channel-heartbeat')).getByRole('option', {
        name: 'Toutes les heures',
      })
    ).toBeInTheDocument();
    await i18n.changeLanguage('en');
  });
});
