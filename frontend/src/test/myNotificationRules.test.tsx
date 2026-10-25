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

  it('renders nothing when the rules cannot be read', async () => {
    const get = vi.spyOn(api, 'getMyPushRules').mockRejectedValue(new Error('403'));
    const { container } = render(<MyNotificationRules />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
