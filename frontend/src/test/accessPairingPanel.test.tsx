import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { SessionProvider } from '../auth/SessionProvider';
import type { AuthPairingCreated, AuthPairingRow, AuthRole, AuthSessionInfo } from '../auth/types';
import { AccessPairingPanel, formatCountdown } from '../components/settings/AccessPairingPanel';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));
// The real component draws the SVG; here only the value it is given matters.
vi.mock('../components/settings/PairingQr', () => ({
  default: ({ value }: { value: string }) => <div data-testid="qr-value">{value}</div>,
}));

const t = i18n.t.bind(i18n);
const PAYLOAD = 'meshloom://pair?d=eyJ2IjoxfQ';

const sessionOf = (role: AuthRole, via: 'lan' | 'ingress' = 'lan'): AuthSessionInfo => ({
  authenticated: true,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  user: { id: 1, username: 'root', display_name: 'Root', role },
  via,
});

const rowOf = (over: Partial<AuthPairingRow>): AuthPairingRow => ({
  id: 'p1',
  label: 'Nadia phone',
  scopes: ['read', 'send'],
  urls: [],
  created_at: Math.floor(Date.now() / 1000),
  expires_at: Math.floor(Date.now() / 1000) + 300,
  created_by: 1,
  username: 'root',
  ...over,
});

const createdOf = (ttl: number): AuthPairingCreated => ({
  ...rowOf({ id: 'p2', label: 'Tablet', created_at: 1_000, expires_at: 1_000 + ttl }),
  code: 'mlp_secret',
  qr_payload: PAYLOAD,
});

async function renderAs(role: AuthRole, via: 'lan' | 'ingress' = 'lan') {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(sessionOf(role, via));
  render(
    <SessionProvider>
      <AccessPairingPanel />
    </SessionProvider>
  );
  await waitFor(() => expect(api.getAuthSession).toHaveBeenCalled());
}

async function fillAndSubmit() {
  fireEvent.click(await screen.findByRole('button', { name: t('settings.access.pairing.add') }));
  fireEvent.change(await screen.findByLabelText(t('settings.access.pairing.label')), {
    target: { value: '  Tablet ' },
  });
  fireEvent.click(screen.getByRole('checkbox', { name: /^read_private/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /^read\b(?!_)/ }));
  fireEvent.change(screen.getByLabelText(t('settings.access.pairing.lifetime')), {
    target: { value: '600' },
  });
  fireEvent.click(screen.getByRole('button', { name: t('settings.access.pairing.create') }));
}

describe('formatCountdown', () => {
  it('formats m:ss and never goes negative', () => {
    expect(formatCountdown(300)).toBe('5:00');
    expect(formatCountdown(59.2)).toBe('1:00');
    expect(formatCountdown(61)).toBe('1:01');
    expect(formatCountdown(-4)).toBe('0:00');
  });
});

describe('AccessPairingPanel', () => {
  beforeEach(() => {
    vi.spyOn(api, 'listAuthPairings').mockResolvedValue([rowOf({})]);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('lists the active pairings for an administrator', async () => {
    await renderAs('admin');
    const item = (await screen.findByText('Nadia phone')).closest('li')!;
    expect(item.textContent).toContain('read');
    expect(item.textContent).toContain(t('settings.access.pairing.owner', { user: 'root' }));
  });

  it('renders nothing for a member and never calls the API', async () => {
    await renderAs('member');
    expect(screen.queryByText(t('settings.access.pairing.title'))).toBeNull();
    expect(api.listAuthPairings).not.toHaveBeenCalled();
  });

  it('explains the ingress limit', async () => {
    await renderAs('admin', 'ingress');
    expect(await screen.findByText(t('settings.access.pairing.ingressNotice'))).toBeInTheDocument();
  });

  it('cancels a listed pairing', async () => {
    const revoke = vi.spyOn(api, 'revokeAuthPairing').mockResolvedValue(undefined);
    await renderAs('admin');
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.access.pairing.cancelNamed', { label: 'Nadia phone' }),
      })
    );
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('p1'));
  });

  it('creates a code, encodes the API payload in the QR and warns not to share it', async () => {
    const create = vi.spyOn(api, 'createAuthPairing').mockResolvedValue(createdOf(600));
    await renderAs('admin');
    await fillAndSubmit();
    expect((await screen.findByTestId('qr-value')).textContent).toBe(PAYLOAD);
    expect(create).toHaveBeenCalledWith({
      label: 'Tablet',
      scopes: ['read', 'read_private'],
      ttl: 600,
    });
    expect(screen.getByText(t('settings.access.pairing.secretWarning'))).toBeInTheDocument();
    expect(screen.getByTestId('pairing-countdown').textContent).toContain('10:00');
    // The secret code itself is never written to the page.
    expect(document.body.textContent).not.toContain('mlp_secret');
  });

  it('hides the QR when the code expires and offers a new one', async () => {
    vi.spyOn(api, 'createAuthPairing').mockResolvedValue(createdOf(3));
    // Only the clock and the interval: the Testing Library waits keep their real timers.
    vi.useFakeTimers({
      toFake: ['Date', 'setInterval', 'clearInterval'],
    });
    await renderAs('admin');
    await fillAndSubmit();
    await screen.findByTestId('qr-value');
    await act(async () => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.queryByTestId('qr-value')).toBeNull();
    expect(screen.getByTestId('pairing-expired')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: t('settings.access.pairing.regenerate') }));
    expect(screen.getByLabelText(t('settings.access.pairing.label'))).toBeInTheDocument();
  });

  it('cancels the code shown in the dialog', async () => {
    vi.spyOn(api, 'createAuthPairing').mockResolvedValue(createdOf(300));
    const revoke = vi.spyOn(api, 'revokeAuthPairing').mockResolvedValue(undefined);
    await renderAs('admin');
    await fillAndSubmit();
    await screen.findByTestId('qr-value');
    fireEvent.click(screen.getByRole('button', { name: t('settings.access.pairing.cancelCode') }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('p2'));
    await waitFor(() => expect(screen.queryByTestId('qr-value')).toBeNull());
  });

  it('revokes a code created while the dialog was closing and does not show it on reopen', async () => {
    let resolve!: (value: AuthPairingCreated) => void;
    vi.spyOn(api, 'createAuthPairing').mockReturnValue(
      new Promise<AuthPairingCreated>((r) => {
        resolve = r;
      })
    );
    const revoke = vi.spyOn(api, 'revokeAuthPairing').mockResolvedValue(undefined);
    await renderAs('admin');
    await fillAndSubmit();
    fireEvent.click(await screen.findByRole('button', { name: t('settings.access.cancel') }));
    await act(async () => {
      resolve(createdOf(300));
    });
    expect(revoke).toHaveBeenCalledWith('p2');
    fireEvent.click(screen.getByRole('button', { name: t('settings.access.pairing.add') }));
    expect(await screen.findByLabelText(t('settings.access.pairing.label'))).toBeInTheDocument();
    expect(screen.queryByTestId('qr-value')).toBeNull();
  });

  it('revokes a still pending code when the dialog is closed', async () => {
    vi.spyOn(api, 'createAuthPairing').mockResolvedValue(createdOf(300));
    const revoke = vi.spyOn(api, 'revokeAuthPairing').mockResolvedValue(undefined);
    await renderAs('admin');
    await fillAndSubmit();
    await screen.findByTestId('qr-value');
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.access.pairing.closeRevokes') })
    );
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('p2'));
    expect(screen.queryByTestId('qr-value')).toBeNull();
  });

  it('drops the code and the QR text from the DOM once expired', async () => {
    vi.spyOn(api, 'createAuthPairing').mockResolvedValue(createdOf(2));
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    await renderAs('admin');
    await fillAndSubmit();
    await screen.findByTestId('qr-value');
    await act(async () => {
      vi.advanceTimersByTime(2500);
    });
    expect(document.body.textContent).not.toContain(PAYLOAD);
    expect(document.body.textContent).not.toContain('mlp_secret');
  });

  it('shows the code as used when the device consumed it', async () => {
    vi.spyOn(api, 'createAuthPairing').mockResolvedValue(createdOf(300));
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    await renderAs('admin');
    await fillAndSubmit();
    await screen.findByTestId('qr-value');
    // The pending list no longer holds p2: the device spent it.
    await act(async () => {
      vi.advanceTimersByTime(5500);
    });
    expect(screen.queryByTestId('qr-value')).toBeNull();
    expect(screen.getByTestId('pairing-expired').textContent).toBe(
      t('settings.access.pairing.used')
    );
  });

  it('treats a not-found cancel as a used code', async () => {
    vi.spyOn(api, 'createAuthPairing').mockResolvedValue(createdOf(300));
    vi.spyOn(api, 'revokeAuthPairing').mockRejectedValue(
      new ApiError('x', 404, { code: 'auth_pairing_not_found', message: 'x' })
    );
    await renderAs('admin');
    await fillAndSubmit();
    await screen.findByTestId('qr-value');
    fireEvent.click(screen.getByRole('button', { name: t('settings.access.pairing.cancelCode') }));
    expect((await screen.findByTestId('pairing-expired')).textContent).toBe(
      t('settings.access.pairing.used')
    );
    expect(screen.queryByTestId('qr-value')).toBeNull();
  });

  it('shows a coded error', async () => {
    vi.spyOn(api, 'createAuthPairing').mockRejectedValue(Object.assign(new Error('x'), {}));
    await renderAs('admin');
    await fillAndSubmit();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
