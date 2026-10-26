import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError } from '../api';
import { ChannelForm, initialDraft } from '../components/settings/notifications/ChannelForm';
import { createBody } from '../components/settings/notifications/notificationsShared';
import i18n from '../i18n';
import type { NotificationChannel, NotificationTypeInfo } from '../notifications/types';

const limits = { per_min: null, per_hour: null, per_day: null };

function entry(overrides: Partial<NotificationTypeInfo>): NotificationTypeInfo {
  return {
    type: 'apprise',
    schema: { properties: {} },
    cloud: false,
    singleton: false,
    preset: false,
    default_limits: limits,
    quiet_hours_exempt_default: false,
    include_message_text_default: 'always',
    templates: true,
    ...overrides,
  };
}

const PREVIEW = {
  dm: 'Alice: See you at the summit [a1, b2, c3]',
  channel: '#hiking -87',
  variables: ['text', 'title'],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('message templates', () => {
  it('lists the variables, previews on the server and sends the typed templates', async () => {
    await i18n.changeLanguage('en');
    const preview = vi.spyOn(api, 'previewNotificationTemplate').mockResolvedValue(PREVIEW);
    const onSubmit = vi.fn();
    render(<ChannelForm entry={entry({})} onSubmit={onSubmit} submitLabel="Save" idPrefix="t" />);

    expect(screen.getByTestId('template-variables')).toHaveTextContent('{hops_backticked}');
    expect(screen.getByTestId('template-variables')).toHaveTextContent('{title}');
    await waitFor(() =>
      expect(screen.getByTestId('template-dm-preview')).toHaveTextContent('Alice')
    );
    expect(screen.getByTestId('template-channel-preview')).toHaveTextContent('#hiking -87');

    fireEvent.change(screen.getByLabelText('Direct messages'), {
      target: { value: '{sender_name}: {text}' },
    });
    await waitFor(() =>
      expect(preview).toHaveBeenLastCalledWith(
        expect.objectContaining({ template_dm: '{sender_name}: {text}', template_channel: '' })
      )
    );
  });

  it('shows the server refusal of a template', async () => {
    await i18n.changeLanguage('en');
    vi.spyOn(api, 'previewNotificationTemplate').mockRejectedValue(
      new ApiError('template_too_long', 400, {
        code: 'template_too_long',
        params: { field: 'template_dm', max: 500 },
      })
    );
    render(<ChannelForm entry={entry({})} onSubmit={vi.fn()} submitLabel="Save" idPrefix="t" />);
    expect(await screen.findByTestId('template-error')).toHaveTextContent('500');
  });

  it('is in French too', async () => {
    await i18n.changeLanguage('fr');
    vi.spyOn(api, 'previewNotificationTemplate').mockResolvedValue(PREVIEW);
    render(<ChannelForm entry={entry({})} onSubmit={vi.fn()} submitLabel="Save" idPrefix="t" />);
    expect(screen.getByText('Modèles de message')).toBeInTheDocument();
    expect(screen.getByLabelText('Messages privés')).toBeInTheDocument();
    await i18n.changeLanguage('en');
  });

  it('has no template fields on a type with its own body', () => {
    vi.spyOn(api, 'previewNotificationTemplate').mockResolvedValue(PREVIEW);
    render(
      <ChannelForm
        entry={entry({ type: 'webhook', templates: false })}
        onSubmit={vi.fn()}
        submitLabel="Save"
        idPrefix="t"
      />
    );
    expect(screen.queryByTestId('channel-templates')).toBeNull();
  });

  it('starts from the stored templates and puts them in the create body', () => {
    const apprise = entry({});
    const stored = {
      template_dm: '{text}',
      template_channel: '{channel_name}',
    } as NotificationChannel;
    const draft = initialDraft(apprise, stored);
    expect([draft.template_dm, draft.template_channel]).toEqual(['{text}', '{channel_name}']);
    expect(createBody(apprise, draft, {})).toMatchObject({
      template_dm: '{text}',
      template_channel: '{channel_name}',
    });
    const body = createBody(entry({ type: 'webhook', templates: false }), draft, {});
    expect(body).not.toHaveProperty('template_dm');
  });
});
