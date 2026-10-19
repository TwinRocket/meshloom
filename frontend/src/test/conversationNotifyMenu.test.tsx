import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import { capabilitiesFor } from '../auth/capabilities';
import { ChatHeader } from '../components/ChatHeader';
import { ConversationNotifyMenu } from '../components/ConversationNotifyMenu';
import { ConversationOverflowMenu } from '../components/ConversationOverflowMenu';
import {
  invalidateConversationNotifications,
  resetConversationNotifications,
} from '../hooks/useConversationNotifications';
import { NotificationStatusProvider } from '../contexts/NotificationStatusContext';
import i18n from '../i18n';
import type { NotificationChannel, NotificationRules } from '../notifications/types';
import type { PathDiscoveryResponse } from '../types';
import { notifyTargetOf } from '../utils/notifyTarget';

const mocks = vi.hoisted(() => ({
  capabilities: null as unknown,
  push: {
    isSupported: false,
    isSubscribed: false,
    subscribe: vi.fn<() => Promise<string | null>>(async () => 'sub-1'),
  },
}));

vi.mock('../auth/SessionProvider', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/SessionProvider')>();
  return {
    ...actual,
    useCapabilities: () => mocks.capabilities ?? actual.useCapabilities(),
  };
});

vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => mocks.push,
}));

const PUBKEY = '11'.repeat(32);
const CONTACT_KEY = `contact-${PUBKEY}`;
const CHANNEL_KEY = 'AB'.repeat(16);

function channel(id: string, name: string, type: string, enabled = true): NotificationChannel {
  return { id, name, type, enabled } as NotificationChannel;
}

const PUSH = channel('builtin:push', 'Browser push', 'web_push');
const NTFY = channel('ntfy', 'My ntfy', 'apprise');
const OFF = channel('slack', 'Old Slack', 'webhook', false);

function rules(overrides: Partial<NotificationRules> = {}): NotificationRules {
  return {
    version: 1,
    paused_until: null,
    events: {
      'message.dm': { channels: ['builtin:push'] },
      'message.channel': { channels: ['builtin:push'] },
    },
    conversations: {},
    ...overrides,
  } as NotificationRules;
}

const contactTarget = notifyTargetOf({ type: 'contact', id: PUBKEY, name: 'Alice' });

function renderMenu(onOpenSettings?: () => void) {
  return render(
    <NotificationStatusProvider
      statuses={{
        'builtin:push': { status: 'connected', last_error: null, last_ok_at: 1 },
        ntfy: { status: 'error', last_error: 'x', last_ok_at: null },
      }}
    >
      <ConversationNotifyMenu target={contactTarget} onOpenSettings={onOpenSettings} />
    </NotificationStatusProvider>
  );
}

async function openBell() {
  const bell = screen.getByRole('button', { name: i18n.t('chatHeader.notifications') });
  fireEvent.click(bell);
  await screen.findByRole('menu');
  return bell;
}

describe('conversation bell', () => {
  beforeEach(() => {
    resetConversationNotifications();
    mocks.capabilities = null;
    mocks.push.isSupported = false;
    mocks.push.isSubscribed = false;
    mocks.push.subscribe.mockClear();
    vi.spyOn(api, 'getNotificationChannels').mockResolvedValue([PUSH, NTFY, OFF]);
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(rules());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('lists one checkbox per enabled channel, with the policy result and a status dot', async () => {
    renderMenu();
    await openBell();

    const push = await screen.findByRole('checkbox', { name: /Browser push/ });
    expect(push).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /My ntfy/ })).not.toBeChecked();
    expect(screen.queryByText('Old Slack')).not.toBeInTheDocument();
    expect(screen.getByTestId('notify-state-builtin:push')).toHaveTextContent(
      i18n.t('chatHeader.notifyState.inherit')
    );
    expect(screen.getByTestId('notify-status-builtin:push')).toHaveAttribute(
      'aria-label',
      i18n.t('settings.notifications.channels.status.connected')
    );
    expect(screen.getByTestId('notify-status-ntfy')).toHaveAttribute(
      'aria-label',
      i18n.t('settings.notifications.channels.status.error')
    );
  });

  it('fills the bell while a channel notifies the conversation', async () => {
    renderMenu();
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: i18n.t('chatHeader.notifications') })
      ).toHaveAttribute('aria-pressed', 'true');
    });
  });

  it('leaves the bell empty when the conversation has an exception off for every channel', async () => {
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(
      rules({ conversations: { [CONTACT_KEY]: { 'builtin:push': false } } })
    );
    renderMenu();
    await openBell();

    await screen.findByRole('checkbox', { name: /Browser push/ });
    expect(screen.getByRole('checkbox', { name: /Browser push/ })).not.toBeChecked();
    expect(screen.getByTestId('notify-state-builtin:push')).toHaveTextContent(
      i18n.t('chatHeader.notifyState.off')
    );
    expect(
      screen.getByRole('button', { name: i18n.t('chatHeader.notifications') })
    ).toHaveAttribute('aria-pressed', 'false');
  });

  it('writes an exception through PUT /rules/conversations/{key}', async () => {
    const put = vi
      .spyOn(api, 'setNotificationConversationRule')
      .mockResolvedValue(rules({ conversations: { [CONTACT_KEY]: { ntfy: true } } }));
    renderMenu();
    await openBell();

    fireEvent.click(await screen.findByRole('checkbox', { name: /My ntfy/ }));

    await waitFor(() => {
      expect(put).toHaveBeenCalledWith(CONTACT_KEY, { ntfy: true });
    });
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /My ntfy/ })).toBeChecked();
    });
    expect(screen.getByTestId('notify-state-ntfy')).toHaveTextContent(
      i18n.t('chatHeader.notifyState.on')
    );
  });

  it('turns a channel off with an explicit exception', async () => {
    const put = vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(rules());
    renderMenu();
    await openBell();

    fireEvent.click(await screen.findByRole('checkbox', { name: /Browser push/ }));

    await waitFor(() => {
      expect(put).toHaveBeenCalledWith(CONTACT_KEY, { 'builtin:push': false });
    });
  });

  it('clears the exception when the box goes back to what the default says', async () => {
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(
      rules({ conversations: { [CONTACT_KEY]: { 'builtin:push': false } } })
    );
    const put = vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(rules());
    renderMenu();
    await openBell();

    fireEvent.click(await screen.findByRole('checkbox', { name: /Browser push/ }));

    await waitFor(() => {
      expect(put).toHaveBeenCalledWith(CONTACT_KEY, null);
    });
  });

  it('drops orphan channel ids from the write', async () => {
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(
      rules({ conversations: { [CONTACT_KEY]: { gone: true } } })
    );
    const put = vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(rules());
    renderMenu();
    await openBell();

    fireEvent.click(await screen.findByRole('checkbox', { name: /My ntfy/ }));

    await waitFor(() => {
      expect(put).toHaveBeenCalledWith(CONTACT_KEY, { ntfy: true });
    });
  });

  it('subscribes the browser before turning builtin:push on', async () => {
    mocks.push.isSupported = true;
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(
      rules({ events: { 'message.dm': { channels: [] }, 'message.channel': { channels: [] } } })
    );
    const put = vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(rules());
    renderMenu();
    await openBell();

    fireEvent.click(await screen.findByRole('checkbox', { name: /Browser push/ }));

    await waitFor(() => {
      expect(put).toHaveBeenCalledWith(CONTACT_KEY, { 'builtin:push': true });
    });
    expect(mocks.push.subscribe).toHaveBeenCalledTimes(1);
    expect(mocks.push.subscribe.mock.invocationCallOrder[0]).toBeLessThan(
      put.mock.invocationCallOrder[0]
    );
  });

  it('pauses with the Do not disturb shortcut and resumes', async () => {
    const patch = vi
      .spyOn(api, 'patchNotificationRules')
      .mockResolvedValue(rules({ paused_until: 4_102_444_800 }));
    renderMenu();
    await openBell();
    await screen.findByRole('checkbox', { name: /Browser push/ });

    const before = Math.floor(Date.now() / 1000);
    fireEvent.click(
      within(screen.getByTestId('notification-pause')).getByRole('button', {
        name: i18n.t('settings.notifications.pause.choice_1h'),
      })
    );

    await waitFor(() => {
      expect(patch).toHaveBeenCalledTimes(1);
    });
    const until = patch.mock.calls[0][0].paused_until as number;
    expect(until).toBeGreaterThanOrEqual(before + 3600);
    expect(until).toBeLessThanOrEqual(before + 3602);

    patch.mockResolvedValue(rules());
    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.notifications.pause.off') })
    );
    await waitFor(() => {
      expect(patch).toHaveBeenLastCalledWith({ paused_until: null });
    });
  });

  it('links to the channels settings and closes', async () => {
    const onOpenSettings = vi.fn();
    renderMenu(onOpenSettings);
    await openBell();

    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('chatHeader.notifyConfigureChannels') })
    );

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('shows a retryable error when the channels cannot be loaded', async () => {
    vi.spyOn(api, 'getNotificationChannels').mockRejectedValue(new Error('down'));
    renderMenu();
    await openBell();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      i18n.t('chatHeader.notifyLoadFailed')
    );
  });

  it('shows the row menu panel too, with the same checkboxes', async () => {
    render(
      <ConversationOverflowMenu
        open
        onOpenChange={vi.fn()}
        pinned={false}
        favorite={false}
        canNotify
        notifyTarget={{ stateKey: CONTACT_KEY, messageType: 'PRIV' }}
        canMute={false}
        canRegion={false}
        canDelete={false}
        isChannel={false}
        onTogglePin={vi.fn()}
        onToggleFavorite={vi.fn()}
        onDelete={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('chatHeader.notifications') }));

    expect(await screen.findByRole('checkbox', { name: /Browser push/ })).toBeChecked();
  });
});

describe('conversation bell access', () => {
  beforeEach(() => resetConversationNotifications());
  const baseProps = {
    contacts: [],
    channels: [],
    config: null,
    onTrace: () => {},
    onPathDiscovery: vi.fn(async () => {
      throw new Error('unused');
    }) as (_: string) => Promise<PathDiscoveryResponse>,
    onToggleFavorite: () => {},
    onTogglePin: () => {},
    onSetChannelFloodScopeOverride: () => {},
    onDeleteChannel: () => {},
    onDeleteContact: () => {},
    conversation: { type: 'channel', id: CHANNEL_KEY, name: '#general' } as const,
  };

  beforeEach(() => {
    vi.spyOn(api, 'getNotificationChannels').mockResolvedValue([PUSH]);
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(rules());
  });

  afterEach(() => {
    mocks.capabilities = null;
    vi.restoreAllMocks();
  });

  it.each(['kiosk', 'viewer', 'member'] as const)('shows no bell to the %s role', (role) => {
    mocks.capabilities = capabilitiesFor(role);
    render(<ChatHeader {...baseProps} />);

    expect(
      screen.queryByRole('button', { name: i18n.t('chatHeader.notifications') })
    ).not.toBeInTheDocument();
    expect(api.getNotificationChannels).not.toHaveBeenCalled();
  });

  it('shows the bell to an administrator', () => {
    mocks.capabilities = capabilitiesFor('admin');
    render(<ChatHeader {...baseProps} />);

    expect(
      screen.getByRole('button', { name: i18n.t('chatHeader.notifications') })
    ).toBeInTheDocument();
  });
});

describe('conversation notifications cache', () => {
  beforeEach(() => {
    resetConversationNotifications();
    mocks.capabilities = null;
    vi.spyOn(api, 'getNotificationChannels').mockResolvedValue([PUSH, NTFY]);
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(rules());
  });

  afterEach(() => {
    resetConversationNotifications();
    vi.restoreAllMocks();
  });

  it('fetches once for several bells and remounts', async () => {
    const first = render(<ConversationNotifyMenu target={contactTarget} />);
    const second = render(<ConversationNotifyMenu target={contactTarget} />);
    await waitFor(() => {
      expect(
        within(first.container).getByRole('button', { name: i18n.t('chatHeader.notifications') })
      ).toHaveAttribute('aria-pressed', 'true');
    });
    first.unmount();
    second.unmount();
    render(<ConversationNotifyMenu target={contactTarget} />);

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: i18n.t('chatHeader.notifications') })
      ).toHaveAttribute('aria-pressed', 'true');
    });
    expect(api.getNotificationChannels).toHaveBeenCalledTimes(1);
    expect(api.getNotificationRules).toHaveBeenCalledTimes(1);
  });

  it('refetches when the bell opens', async () => {
    render(<ConversationNotifyMenu target={contactTarget} />);
    await waitFor(() => expect(api.getNotificationRules).toHaveBeenCalledTimes(1));
    await openBell();
    await waitFor(() => expect(api.getNotificationRules).toHaveBeenCalledTimes(2));
  });

  it('refetches on notification_channel_changed and updates a mounted bell', async () => {
    render(<ConversationNotifyMenu target={contactTarget} />);
    await openBell();
    await screen.findByRole('checkbox', { name: /Browser push/ });
    expect(screen.queryByRole('checkbox', { name: /My ntfy/ })).toBeInTheDocument();
    const calls = vi.mocked(api.getNotificationChannels).mock.calls.length;

    vi.mocked(api.getNotificationChannels).mockResolvedValue([PUSH]);
    invalidateConversationNotifications();

    await waitFor(() => {
      expect(screen.queryByRole('checkbox', { name: /My ntfy/ })).not.toBeInTheDocument();
    });
    expect(api.getNotificationChannels).toHaveBeenCalledTimes(calls + 1);
  });

  it('does not fetch on a change when no bell has loaded', () => {
    invalidateConversationNotifications();
    expect(api.getNotificationChannels).not.toHaveBeenCalled();
  });

  it('keeps the answer of a write in the cache, shown before the refresh on open', async () => {
    vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(
      rules({ conversations: { [CONTACT_KEY]: { ntfy: true } } })
    );
    const first = render(<ConversationNotifyMenu target={contactTarget} />);
    await openBell();
    fireEvent.click(await screen.findByRole('checkbox', { name: /My ntfy/ }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /My ntfy/ })).toBeChecked());
    first.unmount();

    const fetches = vi.mocked(api.getNotificationRules).mock.calls.length;
    vi.mocked(api.getNotificationRules).mockReturnValue(new Promise(() => {}));

    render(<ConversationNotifyMenu target={contactTarget} />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('chatHeader.notifications') }));
    // Served from the cache before the refresh on open answers.
    expect(await screen.findByRole('checkbox', { name: /My ntfy/ })).toBeChecked();
    expect(api.getNotificationRules).toHaveBeenCalledTimes(fetches + 1);
  });
});
