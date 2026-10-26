import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { api } from '../api';
import { NotificationRulesPanel } from '../components/settings/notifications/NotificationRulesPanel';
import {
  draftError,
  emptyKeywordDraft,
  inputOf,
  withDraft,
} from '../components/settings/notifications/keywordsShared';
import { useNotificationChannels } from '../components/settings/notifications/useNotificationChannels';
import i18n from '../i18n';
import type {
  NotificationChannel,
  NotificationKeywordRule,
  NotificationRules,
} from '../notifications/types';
import type { Channel, Contact } from '../types';

const limits = { per_min: null, per_hour: null, per_day: null };

function channel(overrides: Partial<NotificationChannel>): NotificationChannel {
  return {
    id: 'ntfy-1',
    type: 'ntfy',
    preset: null,
    name: 'Phone',
    enabled: true,
    language: 'auto',
    config: {},
    secret_fields: [],
    secret_unreadable: false,
    allow_private_network: false,
    include_message_text: 'public_only',
    verify_tls: true,
    quiet_hours_exempt: false,
    rate_per_min: null,
    rate_per_hour: null,
    rate_per_day: null,
    limits,
    sort_order: 1,
    created_at: 1,
    paused_until: null,
    failure_count: 0,
    deletable: true,
    status: { status: 'connected', last_error: null, last_ok_at: null },
    ...overrides,
  };
}

const PUSH = channel({ id: 'builtin:push', type: 'web_push', name: 'Web Push', deletable: false });
const NTFY = channel({});

const RULE: NotificationKeywordRule = {
  id: 'kw_1',
  pattern: 'secours',
  match: 'word',
  scope: 'all',
  channels: ['ntfy-1'],
  cooldown_s: 300,
};

const RULES: NotificationRules = {
  version: 1,
  paused_until: null,
  events: {},
  conversations: {},
  keywords: [RULE],
  watched_nodes: [],
  radio_offline_grace_s: 60,
  mention_whole_word: false,
  push_allow_private_endpoints: false,
  host_timezone: 'UTC',
};

const CONTACTS = [{ public_key: 'aa11', name: 'Alice' }] as unknown as Contact[];
const CHAT_CHANNELS = [{ key: 'bb22', name: '#ops' }] as unknown as Channel[];

function Harness() {
  const state = useNotificationChannels();
  return <NotificationRulesPanel state={state} contacts={CONTACTS} chatChannels={CHAT_CHANNELS} />;
}

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

function mockApi(rules: NotificationRules = RULES) {
  vi.spyOn(api, 'getNotificationChannels').mockResolvedValue([PUSH, NTFY]);
  vi.spyOn(api, 'getNotificationTypes').mockResolvedValue([]);
  vi.spyOn(api, 'getNotificationRules').mockResolvedValue(rules);
  vi.spyOn(api, 'getNotificationEvents').mockResolvedValue({ contract_version: 1, events: [] });
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});
afterEach(() => {
  vi.restoreAllMocks();
  toastMocks.error.mockClear();
});

describe('keyword rules helpers', () => {
  it('validates the pattern, the scope and the cooldown', () => {
    const draft = emptyKeywordDraft();
    expect(draftError(draft)).toBe('pattern');
    expect(draftError({ ...draft, pattern: '  ' })).toBe('pattern');
    expect(draftError({ ...draft, pattern: 'x'.repeat(65) })).toBe('pattern');
    expect(draftError({ ...draft, pattern: 'x'.repeat(64) })).toBeNull();
    expect(draftError({ ...draft, pattern: 'a', scopeKind: 'chosen' })).toBe('scope');
    expect(draftError({ ...draft, pattern: 'a', cooldownMin: 1441 })).toBe('cooldown');
    expect(draftError({ ...draft, pattern: 'a', cooldownMin: 0 })).toBeNull();
  });

  it('builds the payload: trimmed NFC pattern, Web Push kept, seconds', () => {
    const input = inputOf({
      ...emptyKeywordDraft(),
      pattern: ' café ',
      channels: ['builtin:push', 'ntfy-1'],
      cooldownMin: 2,
    });
    expect(input).toEqual({
      pattern: 'café',
      match: 'contains',
      scope: 'all',
      channels: ['builtin:push', 'ntfy-1'],
      cooldown_s: 120,
    });
    const list = withDraft([RULE], { ...emptyKeywordDraft(), id: 'kw_1', pattern: 'sos' });
    expect(list).toHaveLength(1);
    expect(list[0].pattern).toBe('sos');
    expect(withDraft([RULE], { ...emptyKeywordDraft(), pattern: 'new' })).toHaveLength(2);
  });
});

describe('KeywordRulesCard', () => {
  it('lists the rules and always shows the forgery warning', async () => {
    mockApi();
    render(<Harness />);
    const card = await screen.findByTestId('keyword-rules');
    expect(within(card).getByText(/secours/)).toBeVisible();
    expect(within(card).getByTestId('keyword-forgery-warning')).toHaveTextContent(
      t('settings.notifications.keywords.forgery')
    );
  });

  it('adds a rule through the dialog and saves the whole list', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.notifications.keywords.add') })
    );
    const dialog = await screen.findByRole('dialog');
    const save = within(dialog).getByRole('button', {
      name: t('settings.notifications.keywords.save'),
    });
    expect(save).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(t('settings.notifications.keywords.pattern')), {
      target: { value: 'Place' },
    });
    fireEvent.change(within(dialog).getByLabelText(t('settings.notifications.keywords.scope')), {
      target: { value: 'chosen' },
    });
    expect(save).toBeDisabled(); // a chosen scope needs a conversation
    fireEvent.click(within(dialog).getByRole('checkbox', { name: '#ops' }));
    // Web Push is offered (administrators' devices only, MSHL-400).
    expect(within(dialog).getByRole('button', { name: 'Web Push' })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Phone' }));
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0].keywords).toEqual([
      expect.objectContaining({ id: 'kw_1', pattern: 'secours' }),
      {
        pattern: 'Place',
        match: 'contains',
        scope: ['channel-bb22'],
        channels: ['ntfy-1'],
        cooldown_s: 300,
      },
    ]);
  });

  it('removes a rule and stops adding at 20', async () => {
    const many = Array.from({ length: 20 }, (_, i) => ({
      ...RULE,
      id: `kw_${i}`,
      pattern: `p${i}`,
    }));
    mockApi({ ...RULES, keywords: many });
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    expect(
      await screen.findByRole('button', { name: t('settings.notifications.keywords.add') })
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.notifications.keywords.remove', { pattern: 'p3' }),
      })
    );
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0][0].keywords).toHaveLength(19);
  });

  it('renders in French with typographic apostrophes', async () => {
    await i18n.changeLanguage('fr');
    mockApi();
    render(<Harness />);
    const warning = await screen.findByTestId('keyword-forgery-warning');
    expect(warning.textContent).toContain('N’importe qui');
    expect(warning.textContent).not.toContain("'");
  });
});
