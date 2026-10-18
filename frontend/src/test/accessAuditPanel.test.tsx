import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import type { AuthAuditEntry, AuthUserRow } from '../auth/types';
import { AccessAuditPanel } from '../components/settings/AccessAuditPanel';
import { AUDIT_FAMILIES, AUDIT_OUTCOMES } from '../components/settings/auditShared';
import i18n from '../i18n';

const t = i18n.t.bind(i18n);

const entry = (over: Partial<AuthAuditEntry>): AuthAuditEntry => ({
  id: 1,
  ts: 1_700_000_000,
  actor_user_id: 1,
  actor_token_id: null,
  action: 'login',
  target: null,
  ip: '10.0.0.2',
  via: 'lan',
  outcome: 'success',
  details: {},
  ...over,
});

const users = [
  { id: 1, username: 'root', display_name: 'Root' },
  { id: 2, username: 'bob', display_name: '' },
] as AuthUserRow[];

describe('AccessAuditPanel', () => {
  beforeEach(() => {
    vi.spyOn(api, 'listAuthUsers').mockResolvedValue(users);
  });
  afterEach(() => vi.restoreAllMocks());

  it('lists translated rows and pages with the opaque cursor', async () => {
    const spy = vi
      .spyOn(api, 'getAuthAudit')
      .mockResolvedValueOnce({
        entries: [
          entry({ id: 5, action: 'user.created', target: 'user:2' }),
          entry({ id: 4, action: 'login', outcome: 'failure', actor_user_id: null, via: 'relay' }),
        ],
        next_cursor: '4',
      })
      .mockResolvedValueOnce({
        entries: [entry({ id: 3, action: 'factory_reset', via: 'cli' })],
        next_cursor: null,
      });
    render(<AccessAuditPanel />);
    const rows = await screen.findAllByTestId('audit-row');
    expect(rows).toHaveLength(2);
    expect(
      within(rows[0]).getByText(t('settings.access.audit.actions.user.created'))
    ).toBeVisible();
    expect(within(rows[0]).getByText(/Root/)).toBeVisible();
    expect(within(rows[0]).getByText(/bob/)).toBeVisible();
    expect(within(rows[1]).getByText(t('settings.access.audit.outcomes.failure'))).toBeVisible();
    expect(
      within(rows[1]).getByText(t('settings.access.audit.anonymous'), { exact: false })
    ).toBeVisible();
    expect(spy).toHaveBeenLastCalledWith(null, 100);

    await userEvent.click(
      screen.getByRole('button', { name: t('settings.access.audit.loadMore') })
    );
    await waitFor(() => expect(screen.getAllByTestId('audit-row')).toHaveLength(3));
    expect(spy).toHaveBeenLastCalledWith('4', 100);
    expect(screen.queryByRole('button', { name: t('settings.access.audit.loadMore') })).toBeNull();
    expect(screen.getByText(t('settings.access.audit.endOfLog'))).toBeVisible();
  });

  it('filters by family, outcome, actor and date', async () => {
    vi.spyOn(api, 'getAuthAudit').mockResolvedValue({
      entries: [
        entry({ id: 4, action: 'bot.created', actor_user_id: 2, ts: 1_700_100_000 }),
        entry({ id: 3, action: 'login', outcome: 'failure', actor_user_id: null }),
        entry({ id: 2, action: 'login', actor_user_id: 1 }),
      ],
      next_cursor: null,
    });
    render(<AccessAuditPanel />);
    await screen.findAllByTestId('audit-row');
    const pick = (label: string, value: string) =>
      userEvent.selectOptions(screen.getByLabelText(t(label)), value);

    await pick('settings.access.audit.filterFamily', 'signin');
    expect(screen.getAllByTestId('audit-row')).toHaveLength(2);
    await pick('settings.access.audit.filterOutcome', 'failure');
    expect(screen.getAllByTestId('audit-row')).toHaveLength(1);
    await pick('settings.access.audit.filterOutcome', '');
    await pick('settings.access.audit.filterActor', '1');
    expect(screen.getAllByTestId('audit-row')).toHaveLength(1);

    await userEvent.click(screen.getByRole('button', { name: t('settings.access.audit.reset') }));
    expect(screen.getAllByTestId('audit-row')).toHaveLength(3);
    const day = new Date(1_700_100_000 * 1000);
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
    await userEvent.type(screen.getByLabelText(t('settings.access.audit.filterFrom')), iso);
    expect(screen.getAllByTestId('audit-row')).toHaveLength(1);
    await pick('settings.access.audit.filterFamily', 'signin');
    expect(screen.getByText(t('settings.access.audit.noMatch'))).toBeVisible();
  });

  it('says so when the log is empty and shows an API error', async () => {
    vi.spyOn(api, 'getAuthAudit').mockResolvedValue({ entries: [], next_cursor: null });
    const { unmount } = render(<AccessAuditPanel />);
    expect(await screen.findByText(t('settings.access.audit.empty'))).toBeVisible();
    unmount();
    vi.spyOn(api, 'getAuthAudit').mockRejectedValue(new Error('boom'));
    render(<AccessAuditPanel />);
    expect(await screen.findByRole('alert')).toBeVisible();
  });

  it('never shows the details of a row', async () => {
    vi.spyOn(api, 'getAuthAudit').mockResolvedValue({
      entries: [entry({ details: { note: 'visible-secret-marker' } })],
      next_cursor: null,
    });
    render(<AccessAuditPanel />);
    await screen.findByTestId('audit-row');
    expect(screen.queryByText(/visible-secret-marker/)).toBeNull();
  });

  it('has a label for every action family member, outcome and via in both languages', () => {
    for (const lng of ['en', 'fr']) {
      const tl = i18n.getFixedT(lng);
      for (const action of Object.values(AUDIT_FAMILIES).flat()) {
        expect(tl(`settings.access.audit.actions.${action}`)).not.toContain('settings.access');
      }
      for (const o of AUDIT_OUTCOMES) {
        expect(tl(`settings.access.audit.outcomes.${o}`)).not.toContain('settings.access');
      }
      for (const v of ['lan', 'ingress', 'trusted', 'relay', 'cli', 'system']) {
        expect(tl(`settings.access.audit.via.${v}`)).not.toContain('settings.access');
      }
    }
  });
});
