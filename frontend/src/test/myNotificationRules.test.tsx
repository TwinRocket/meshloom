import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { api } from '../api';
import { MyNotificationRules } from '../components/settings/notifications/MyNotificationRules';
import i18n from '../i18n';
import type { MyPushRules } from '../notifications/types';

function rules(overrides: Partial<MyPushRules> = {}): MyPushRules {
  return {
    events: {
      'message.dm': { default: true, choice: null },
      'message.channel': { default: false, choice: null },
      'contact.first_seen': { default: false, choice: null },
    },
    contact_types: {
      companion: { default: true, choice: null },
      repeater: { default: false, choice: null },
      sensor: { default: false, choice: null },
    },
    conversations: {},
    pause: { active: false, until: null },
    ...overrides,
  };
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('MyNotificationRules (MSHL-321)', () => {
  it('shows the default of each row and saves a choice', async () => {
    vi.spyOn(api, 'getMyPushRules').mockResolvedValue(rules());
    const patch = vi.spyOn(api, 'patchMyPushRules').mockResolvedValue(
      rules({
        events: {
          'message.dm': { default: true, choice: false },
          'message.channel': { default: false, choice: null },
          'contact.first_seen': { default: false, choice: null },
        },
      })
    );

    render(<MyNotificationRules />);

    const dm = (await screen.findAllByRole('combobox'))[0] as HTMLSelectElement;
    expect(dm.value).toBe('default');
    expect(dm).toHaveDisplayValue('Default: notify');
    fireEvent.change(dm, { target: { value: 'off' } });
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ events: { 'message.dm': false } }));
    await waitFor(() =>
      expect((screen.getAllByRole('combobox')[0] as HTMLSelectElement).value).toBe('off')
    );
  });

  it('sets a contact type apart and resets to the defaults', async () => {
    vi.spyOn(api, 'getMyPushRules').mockResolvedValue(
      rules({
        contact_types: {
          companion: { default: true, choice: null },
          repeater: { default: false, choice: true },
          sensor: { default: false, choice: null },
        },
      })
    );
    const patch = vi.spyOn(api, 'patchMyPushRules').mockResolvedValue(rules());
    render(<MyNotificationRules />);

    const companion = await screen.findByLabelText(/Companions/);
    fireEvent.change(companion, { target: { value: 'on' } });
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ contact_types: { companion: true } }));
  });

  it('resets every choice to the defaults', async () => {
    vi.spyOn(api, 'getMyPushRules').mockResolvedValue(
      rules({
        events: {
          'message.dm': { default: true, choice: false },
          'message.channel': { default: false, choice: null },
          'contact.first_seen': { default: false, choice: null },
        },
      })
    );
    const reset = vi.spyOn(api, 'resetMyPushRules').mockResolvedValue(rules());

    render(<MyNotificationRules />);

    fireEvent.click(await screen.findByRole('button', { name: 'Follow the defaults again' }));
    await waitFor(() => expect(reset).toHaveBeenCalled());
  });

  it('pauses my devices for a while, until I resume, and resumes', async () => {
    vi.spyOn(api, 'getMyPushRules').mockResolvedValue(rules());
    const pause = vi
      .spyOn(api, 'pauseMyPush')
      .mockResolvedValue(rules({ pause: { active: true, until: null } }));
    const resume = vi.spyOn(api, 'resumeMyPush').mockResolvedValue(rules());
    render(<MyNotificationRules />);

    expect(await screen.findByText('Notifications are on.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '1 hour' }));
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));
    const until = pause.mock.calls[0][0] as number;
    expect(until - Math.floor(Date.now() / 1000)).toBeGreaterThan(3500);

    fireEvent.click(screen.getByRole('button', { name: 'Until I resume' }));
    await waitFor(() => expect(pause).toHaveBeenLastCalledWith(null));
    expect(await screen.findByText('Paused until you resume')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Resume now' }));
    await waitFor(() => expect(resume).toHaveBeenCalled());
    expect(await screen.findByText('Notifications are on.')).toBeInTheDocument();
  });

  it('adds, changes and removes a conversation exception', async () => {
    const key = 'ab'.repeat(32);
    vi.spyOn(api, 'getMyPushRules').mockResolvedValue(
      rules({ conversations: { [`contact-${key}`]: false } })
    );
    const patch = vi
      .spyOn(api, 'patchMyPushRules')
      .mockResolvedValue(rules({ conversations: { [`contact-${key}`]: true } }));
    const contacts = [{ public_key: key, name: 'Léa', type: 1 }] as never;
    const channels = [{ key: 'AA'.repeat(16), name: 'Paris', is_hashtag: false }] as never;
    render(<MyNotificationRules contacts={contacts} chatChannels={channels} />);

    const row = await screen.findByLabelText('Léa');
    fireEvent.change(row, { target: { value: 'on' } });
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({ conversations: { [`contact-${key}`]: true } })
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remove my exception for Léa' }));
    await waitFor(() =>
      expect(patch).toHaveBeenLastCalledWith({ conversations: { [`contact-${key}`]: null } })
    );

    fireEvent.change(screen.getByLabelText('Choose a conversation'), {
      target: { value: `channel-${'AA'.repeat(16)}` },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Mute for me' }));
    await waitFor(() =>
      expect(patch).toHaveBeenLastCalledWith({
        conversations: { [`channel-${'AA'.repeat(16)}`]: false },
      })
    );
  });

  it('is available in French', async () => {
    await i18n.changeLanguage('fr');
    vi.spyOn(api, 'getMyPushRules').mockResolvedValue(rules());
    render(<MyNotificationRules />);
    expect(await screen.findByText("Jusqu'à la reprise")).toBeInTheDocument();
    expect(screen.getByText('Aucune exception de conversation.')).toBeInTheDocument();
  });

  it('renders nothing when the rules cannot be read', async () => {
    const get = vi.spyOn(api, 'getMyPushRules').mockRejectedValue(new Error('403'));
    const { container } = render(<MyNotificationRules />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
