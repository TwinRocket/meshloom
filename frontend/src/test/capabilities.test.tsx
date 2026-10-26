import React from 'react';
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './eSlices';
import { api, ApiError } from '../api';
import { capabilitiesFor, UNRESTRICTED, visibleSettingsSections } from '../auth/capabilities';
import { forbiddenIsToasted, reportForbidden } from '../auth/authEvents';
import { SessionProvider, useCapabilities } from '../auth/SessionProvider';
import type { AuthRole, AuthSessionInfo } from '../auth/types';
import { BottomNav } from '../components/BottomNav';
import { ConversationListView } from '../components/ConversationListView';
import { ConversationPane } from '../components/ConversationPane';
import { DesktopRail } from '../components/DesktopRail';
import { SettingsIndexView } from '../components/SettingsIndexView';
import { toolAllowed } from '../components/navDestinations';
import { SETTINGS_SECTION_ORDER } from '../components/settings/settingsConstants';
import { toast } from '../components/ui/sonner';
import { useAppSettings } from '../hooks/useAppSettings';
import { useOssUpdates } from '../hooks/useOssUpdates';
import { useRadioControl } from '../hooks/useRadioControl';
import { usePushSubscription } from '../hooks/usePushSubscription';
import i18n from '../i18n';
import type { Channel, Contact, HealthStatus, RadioConfig } from '../types';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));
vi.mock('../components/ChatHeader', () => ({
  ChatHeader: () => <div data-testid="chat-header" />,
}));
vi.mock('../components/MessageList', () => ({
  MessageList: () => <div data-testid="message-list" />,
}));
vi.mock('../components/MessageInput', () => ({
  MessageInput: React.forwardRef(() => <div data-testid="message-input" />),
}));
vi.mock('../components/RepeaterDashboard', () => ({
  RepeaterDashboard: () => <div data-testid="repeater-dashboard" />,
}));
vi.mock('../components/RoomServerPanel', () => ({
  RoomServerPanel: () => <div data-testid="room-server-panel" />,
}));

const sessionOf = (role: AuthRole): AuthSessionInfo => ({
  authenticated: true,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  auth_compat: { basic: false },
  user: { id: 1, username: role, display_name: role, role },
  via: 'lan',
});

function stubSession(role: AuthRole) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(sessionOf(role)), { status: 200 }))
  );
}

/** Renders `ui` under a real session gate that answers as `role`. */
async function renderAs(role: AuthRole, ui: React.ReactElement) {
  stubSession(role);
  const view = render(<SessionProvider>{ui}</SessionProvider>);
  await waitFor(() => expect(screen.queryByText(i18n.t('auth.loading'))).toBeNull());
  // The session lands from a promise, outside `act`: React runs the gate's passive effects
  // (its event listeners) after the DOM changed. Flush them before a test reports an event.
  await act(async () => {});
  return view;
}

function wrapperFor(role: AuthRole) {
  stubSession(role);
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <SessionProvider>{children}</SessionProvider>;
  };
}

/** A hook under a session of `role`, read once the gate has let it mount. */
async function hookAs<T>(role: AuthRole, hook: () => T) {
  const Wrapper = wrapperFor(role);
  const gated = renderHook(hook, { wrapper: Wrapper });
  await waitFor(() => expect(gated.result.current).not.toBeNull());
  // Same as `renderAs`: the hook's mount effects run after the gate's DOM change.
  await act(async () => {});
  return gated;
}

const channel = {
  key: 'C1',
  name: '#alpha',
  is_hashtag: true,
  on_radio: true,
  last_read_at: null,
  favorite: false,
  muted: false,
} as Channel;
const contact = {
  public_key: 'aa'.repeat(32),
  name: 'Alice',
  type: 1,
  flags: 0,
  direct_path: null,
  direct_path_len: 0,
  direct_path_hash_mode: 0,
  favorite: false,
} as Contact;
const repeater = { ...contact, public_key: 'bb'.repeat(32), name: 'Hill', type: 2 } as Contact;
const config = { name: 'Radio', lat: 1, lon: 2 } as RadioConfig;
const health = { radio_connected: true } as HealthStatus;

beforeEach(async () => {
  await i18n.changeLanguage('en');
  vi.clearAllMocks();
});
afterEach(() => vi.unstubAllGlobals());

describe('role capabilities', () => {
  it('mirrors the server role table (app/auth/permissions.py)', () => {
    const grants = (role: AuthRole) =>
      (['read', 'read_private', 'send', 'repeater_admin', 'admin'] as const).filter((c) =>
        capabilitiesFor(role).has(c)
      );
    expect(grants('kiosk')).toEqual(['read']);
    expect(grants('viewer')).toEqual(['read', 'read_private']);
    expect(grants('member')).toEqual(['read', 'read_private', 'send']);
    expect(grants('admin')).toEqual(['read', 'read_private', 'send', 'repeater_admin', 'admin']);
  });

  it('gives a session its role capabilities and leaves a bare render unrestricted', async () => {
    const seen: Record<string, boolean> = {};
    function Probe() {
      const c = useCapabilities();
      seen.send = c.canSend;
      seen.settings = c.canOpenSettings;
      return null;
    }
    await renderAs('viewer', <Probe />);
    expect(seen).toEqual({ send: false, settings: true });
    expect(UNRESTRICTED.isAdmin).toBe(true);
    expect(UNRESTRICTED.role).toBeNull();
  });

  it('opens settings sections by role: all for admin, Local, Users & Access (and Share uploads and Extensions, read-only, for member) for member and viewer, none for kiosk', () => {
    const sections = (role: AuthRole) =>
      visibleSettingsSections(capabilitiesFor(role), SETTINGS_SECTION_ORDER);
    expect(sections('admin')).toEqual(SETTINGS_SECTION_ORDER);
    expect(sections('member')).toEqual(['local', 'access', 'share', 'extensions']);
    expect(sections('viewer')).toEqual(['local', 'access']);
    expect(sections('kiosk')).toEqual([]);
  });

  it('keeps the tools that call refused routes away from the roles that cannot use them', () => {
    expect(toolAllowed('test', capabilitiesFor('member'))).toBe(false);
    expect(toolAllowed('test', capabilitiesFor('admin'))).toBe(true);
    expect(toolAllowed('trace', capabilitiesFor('viewer'))).toBe(false);
    expect(toolAllowed('trace', capabilitiesFor('member'))).toBe(true);
    expect(toolAllowed('feed', capabilitiesFor('kiosk'))).toBe(true);
  });
});

describe('settings entry points', () => {
  it.each([
    ['admin', true],
    ['member', true],
    ['viewer', true],
    ['kiosk', false],
  ] as const)('the settings door of the %s role: %s', async (role, shown) => {
    await renderAs(
      role,
      <>
        <BottomNav active={null} unreadTotal={0} onSelect={vi.fn()} />
        <DesktopRail
          active={null}
          unreadTotal={0}
          onSelect={vi.fn()}
          health={health}
          onOpenRadioStatus={vi.fn()}
          onSelectTool={vi.fn()}
        />
      </>
    );
    const settings = i18n.t('bottomNav.settings');
    const doors = screen.queryAllByRole('button', { name: settings, hidden: true });
    const labelled = screen.queryAllByText(settings);
    expect(doors.length + labelled.length > 0).toBe(shown);
  });

  it('lists every section for an administrator', async () => {
    await renderAs('admin', <SettingsIndexView onSelectSection={vi.fn()} />);
    expect(screen.getAllByRole('button', { name: /./ }).length).toBeGreaterThanOrEqual(
      SETTINGS_SECTION_ORDER.length
    );
    expect(screen.getByText(i18n.t('settingsNav.fanout'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settingsNav.database'))).toBeInTheDocument();
  });

  it.each(['member', 'viewer'] as const)(
    'offers the %s role Local configuration and no administration section',
    async (role) => {
      await renderAs(role, <SettingsIndexView onSelectSection={vi.fn()} />);
      expect(screen.getByText(i18n.t('settingsNav.local'))).toBeInTheDocument();
      expect(screen.queryByText(i18n.t('settingsNav.fanout'))).toBeNull();
      expect(screen.queryByText(i18n.t('settingsNav.database'))).toBeNull();
      expect(screen.queryByText(i18n.t('settingsNav.radio'))).toBeNull();
      expect(screen.queryByText(i18n.t('settingsNav.updates'))).toBeNull();
    }
  );

  it('offers the kiosk no section at all', async () => {
    await renderAs('kiosk', <SettingsIndexView onSelectSection={vi.fn()} />);
    for (const section of SETTINGS_SECTION_ORDER) {
      expect(
        screen.queryByText(i18n.t(`settingsNav.${section === 'radio-app' ? 'radioApp' : section}`))
      ).toBeNull();
    }
  });
});

describe('conversation list', () => {
  function list() {
    return (
      <ConversationListView
        contacts={[contact]}
        channels={[channel]}
        unreadCounts={{}}
        mentions={{}}
        lastMessageTimes={{}}
        lastMessagePreviews={{}}
        onSelectConversation={vi.fn()}
        onNewMessage={vi.fn()}
      />
    );
  }

  it('shows a member the direct messages and the new-conversation action', async () => {
    await renderAs('member', list());
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('conversationList.new') })
    ).toBeInTheDocument();
  });

  it('shows a viewer the direct messages but no way to start a conversation', async () => {
    await renderAs('viewer', list());
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('conversationList.new') })).toBeNull();
  });

  it('shows the kiosk channels only: no direct message, no Direct chip, no new action', async () => {
    await renderAs('kiosk', list());
    const group = screen.getByRole('group', { name: i18n.t('conversationList.filterLabel') });
    expect(screen.getByText('#alpha')).toBeInTheDocument();
    expect(screen.queryByText('Alice')).toBeNull();
    expect(
      within(group).queryByRole('button', { name: new RegExp(i18n.t('conversationList.direct')) })
    ).toBeNull();
    expect(screen.queryByRole('button', { name: i18n.t('conversationList.new') })).toBeNull();
  });
});

describe('conversation pane', () => {
  const props = (
    conversation: React.ComponentProps<typeof ConversationPane>['activeConversation']
  ) =>
    ({
      activeConversation: conversation,
      contacts: [contact, repeater],
      channels: [channel],
      rawPackets: [],
      rawPacketStatsSession: {
        sessionStartedAt: 0,
        totalObservedPackets: 0,
        trimmedObservationCount: 0,
        observations: [],
      },
      config,
      health,
      messages: [],
      messagesLoading: false,
      loadingOlder: false,
      hasOlderMessages: false,
      targetMessageId: null,
      hasNewerMessages: false,
      loadingNewer: false,
      messageInputRef: { current: null },
      onTrace: vi.fn(),
      onRunTracePath: vi.fn(),
      onPathDiscovery: vi.fn(),
      onToggleFavorite: vi.fn(),
      onDeleteContact: vi.fn(),
      onDeleteChannel: vi.fn(),
      onSelectConversation: vi.fn(),
      onOpenContactInfo: vi.fn(),
      onOpenChannelInfo: vi.fn(),
      onSenderClick: vi.fn(),
      onLoadOlder: vi.fn(),
      onTargetReached: vi.fn(),
      onLoadNewer: vi.fn(),
      onJumpToBottom: vi.fn(),
      onDismissUnreadMarker: vi.fn(),
      onSendMessage: vi.fn(),
      trackedTelemetryRepeaters: [],
      onToggleTrackedTelemetry: vi.fn(),
      repeaterAutoLoginKey: null,
      onClearRepeaterAutoLogin: vi.fn(),
    }) as unknown as React.ComponentProps<typeof ConversationPane>;
  const inChannel = { type: 'channel', id: channel.key, name: '#alpha' } as const;

  it.each([
    ['admin', true],
    ['member', true],
    ['viewer', false],
    ['kiosk', false],
  ] as const)('the composer for the %s role: %s', async (role, shown) => {
    await renderAs(role, <ConversationPane {...props(inChannel)} />);
    expect(screen.getByTestId('message-list')).toBeInTheDocument();
    expect(screen.queryByTestId('message-input') !== null).toBe(shown);
  });

  it('opens the repeater dashboard for an administrator only', async () => {
    const repeaterConversation = {
      type: 'contact',
      id: repeater.public_key,
      name: 'Hill',
    } as const;
    await renderAs('admin', <ConversationPane {...props(repeaterConversation)} />);
    expect(await screen.findByTestId('repeater-dashboard')).toBeInTheDocument();
  });

  it.each(['member', 'viewer'] as const)(
    'replaces the repeater dashboard with a notice for the %s role',
    async (role) => {
      const repeaterConversation = {
        type: 'contact',
        id: repeater.public_key,
        name: 'Hill',
      } as const;
      await renderAs(role, <ConversationPane {...props(repeaterConversation)} />);
      expect(screen.queryByTestId('repeater-dashboard')).toBeNull();
      expect(screen.getByTestId('repeater-admin-forbidden')).toHaveTextContent(
        i18n.t('capabilities.repeaterAdminOnly')
      );
    }
  );
});

describe('legacy favourites migration', () => {
  const view = {
    ui_preferences: { theme: '', nav_rail: [] },
    last_message_times: {},
    flood_scope: '',
    known_regions: [],
    blocked_keys: [],
    blocked_names: [],
    directory_available: false,
  };

  beforeEach(() => {
    localStorage.setItem('meshloom-favorites', JSON.stringify([{ type: 'channel', id: 'C1' }]));
  });
  afterEach(() => {
    localStorage.removeItem('meshloom-favorites');
    vi.restoreAllMocks();
  });

  it.each(['viewer', 'kiosk'] as const)(
    'does not toggle favourites, nor wipe the local list, as the %s role',
    async (role) => {
      vi.spyOn(api, 'getSettingsView').mockResolvedValue(view);
      const toggle = vi.spyOn(api, 'toggleFavorite').mockResolvedValue({} as never);
      const { result } = await hookAs(role, () => useAppSettings());
      await act(async () => {
        await result.current.fetchAppSettings();
      });
      expect(toggle).not.toHaveBeenCalled();
      expect(localStorage.getItem('meshloom-favorites')).not.toBeNull();
    }
  );

  it('keeps the local list when the server refuses the toggle', async () => {
    vi.spyOn(api, 'getSettingsView').mockResolvedValue(view);
    const toggle = vi
      .spyOn(api, 'toggleFavorite')
      .mockRejectedValue(new ApiError('forbidden', 403));
    const { result } = await hookAs('member', () => useAppSettings());
    await act(async () => {
      await result.current.fetchAppSettings();
    });
    await waitFor(() => expect(toggle).toHaveBeenCalled());
    // Let the rejection settle before reading what the migration left behind.
    await act(async () => {});
    expect(localStorage.getItem('meshloom-favorites')).not.toBeNull();
  });
});

describe('startup calls', () => {
  const adminOnly = () => [
    vi.spyOn(api, 'getSettings').mockResolvedValue({} as never),
    vi.spyOn(api, 'getRadioConfig').mockResolvedValue({} as never),
    vi.spyOn(api, 'getPushPreferences').mockResolvedValue({} as never),
    vi.spyOn(api, 'getUpdates').mockResolvedValue({} as never),
  ];

  afterEach(() => vi.restoreAllMocks());

  it.each(['member', 'viewer', 'kiosk'] as const)(
    'makes no administrator-only call as the %s role',
    async (role) => {
      const spies = adminOnly();
      vi.spyOn(api, 'getPushSubscriptions').mockResolvedValue([]);
      vi.spyOn(api, 'getHealth').mockResolvedValue({} as never);
      const { result } = await hookAs(role, () => {
        const settings = useAppSettings();
        const radio = useRadioControl();
        useOssUpdates({ enabled: useCapabilities().isAdmin });
        usePushSubscription();
        return { settings, radio };
      });
      await result.current.settings.fetchAppSettings();
      await result.current.radio.fetchConfig();
      for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    }
  );

  it.each(['member', 'viewer', 'kiosk'] as const)(
    'reads the readable settings view as the %s role, and cannot save settings',
    async (role) => {
      const view = vi.spyOn(api, 'getSettingsView').mockResolvedValue({
        ui_preferences: { theme: 'dark', nav_rail: [] },
        last_message_times: {},
        flood_scope: '',
        known_regions: [],
        blocked_keys: ['ab'],
        blocked_names: ['spam'],
        directory_available: false,
      });
      const update = vi.spyOn(api, 'updateSettings').mockResolvedValue({} as never);
      const { result } = await hookAs(role, () => useAppSettings());
      await act(async () => {
        await result.current.fetchAppSettings();
      });
      expect(view).toHaveBeenCalled();
      expect(result.current.appSettings?.blocked_keys).toEqual(['ab']);
      expect(result.current.appSettings?.blocked_names).toEqual(['spam']);
      await act(async () => {
        await result.current.handleSaveAppSettings({ flood_scope: 'x' });
      });
      expect(update).not.toHaveBeenCalled();
    }
  );

  it('still reads the settings and the radio configuration as an administrator', async () => {
    const settings = vi
      .spyOn(api, 'getSettings')
      .mockResolvedValue({ last_message_times: {} } as never);
    const radio = vi.spyOn(api, 'getRadioConfig').mockResolvedValue({} as never);
    const { result } = await hookAs('admin', () => {
      const s = useAppSettings();
      const r = useRadioControl();
      return { s, r };
    });
    await result.current.s.fetchAppSettings();
    await result.current.r.fetchConfig();
    expect(settings).toHaveBeenCalled();
    expect(radio).toHaveBeenCalled();
  });
});

describe('refused actions', () => {
  it('tells an action that was refused, never a read or a direct-message state', () => {
    expect(forbiddenIsToasted({ method: 'POST', permission: 'send' })).toBe(true);
    expect(forbiddenIsToasted({ method: 'PATCH', permission: 'admin' })).toBe(true);
    expect(forbiddenIsToasted({ method: 'GET', permission: 'admin' })).toBe(false);
    expect(forbiddenIsToasted({ method: 'POST', permission: 'read_private' })).toBe(false);
  });

  it('toasts the translated message, not the server text, once for a burst', async () => {
    await renderAs('viewer', <p>app</p>);
    reportForbidden({ method: 'POST', permission: 'send' });
    reportForbidden({ method: 'POST', permission: 'send' });
    expect(toast.error).toHaveBeenCalledWith(i18n.t('errors.auth_forbidden'), {
      id: 'auth_forbidden',
    });
    expect(i18n.t('errors.auth_forbidden')).not.toMatch(/Not allowed for this account/);
  });

  it('stays silent on a refused read', async () => {
    await renderAs('kiosk', <p>app</p>);
    reportForbidden({ method: 'GET', permission: 'read_private' });
    expect(toast.error).not.toHaveBeenCalled();
  });
});
