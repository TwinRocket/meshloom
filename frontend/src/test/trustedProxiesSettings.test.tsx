import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));

import {
  TrustedProxiesSettings,
  isProxyEntry,
  proxyEntryRisk,
} from '../components/settings/TrustedProxiesSettings';
import { behindUntrustedHttpsProxy, type AuthSettings } from '../auth/types';
import { SessionProvider } from '../auth/SessionProvider';
import i18n from '../i18n';

const saveButton = () =>
  screen.getByRole('button', { name: i18n.t('settings.trustedProxies.save') });

describe('isProxyEntry', () => {
  it.each(['127.0.0.1', ' 10.0.0.0/8 ', '172.18.0.0/16', '::1', 'fd00:1::/64', '2001:db8::1/128'])(
    'accepts %s',
    (raw) => {
      expect(isProxyEntry(raw)).toBe(true);
    }
  );

  it.each([
    '0.0.0.0/0',
    '::/0',
    '0.0.0.0/1',
    '10.0.0.0/7',
    'fd00::/8',
    '::ffff:10.0.0.1',
    '*',
    'proxy.lan',
    '10.0.0.0/33',
    '::1/129',
    '256.0.0.1',
    '10.0.0.1/8/8',
    '10.0.0.1/x',
    'http://[::1]',
  ])('refuses %s', (raw) => {
    expect(isProxyEntry(raw)).toBe(false);
  });
});

describe('proxyEntryRisk', () => {
  it.each([
    ['172.18.0.0/16', 'network'],
    ['10.0.0.0/8', 'network'],
    ['fd00:1::/64', 'network'],
    ['172.17.0.1', 'gateway'],
    ['172.20.0.1', 'gateway'],
    ['192.168.65.1', 'gateway'],
    ['172.20.0.10', null],
    ['127.0.0.1', null],
    ['::1', null],
    ['192.168.1.1', null],
  ])('%s is %s', (raw, risk) => {
    expect(proxyEntryRisk(raw)).toBe(risk);
  });
});

describe('behindUntrustedHttpsProxy', () => {
  it('is true only on https with a server that saw http', () => {
    expect(behindUntrustedHttpsProxy({ secure: false }, 'https:')).toBe(true);
    expect(behindUntrustedHttpsProxy({ secure: true }, 'https:')).toBe(false);
    expect(behindUntrustedHttpsProxy({ secure: false }, 'http:')).toBe(false);
    expect(behindUntrustedHttpsProxy({}, 'https:')).toBe(false);
    expect(behindUntrustedHttpsProxy(null, 'https:')).toBe(false);
  });
});

describe('TrustedProxiesSettings', () => {
  beforeEach(() => {
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
  });

  it('stays hidden when the settings cannot be read (not an admin)', async () => {
    const load = vi.fn(async (): Promise<AuthSettings> => {
      throw new Error('forbidden');
    });
    const { container } = render(<TrustedProxiesSettings load={load} />);
    await waitFor(() => expect(load).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('says an empty list trusts no proxy, then saves the canonical list', async () => {
    const load = vi.fn(async () => ({ trusted_proxies: [], audit_retention_days: 366 }));
    const save = vi.fn(async () => ({
      trusted_proxies: ['127.0.0.1/32'],
      audit_retention_days: 366,
    }));
    render(<TrustedProxiesSettings load={load} save={save} />);

    expect(await screen.findByText(i18n.t('settings.trustedProxies.none'))).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: ' 127.0.0.1 \n\n127.0.0.1' } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(save).toHaveBeenCalledWith({ trusted_proxies: ['127.0.0.1'] }));
    await waitFor(() => expect(textarea).toHaveValue('127.0.0.1/32'));
    expect(mocks.toast.success).toHaveBeenCalled();
    expect(saveButton()).toBeDisabled();
  });

  it('refuses an invalid entry before sending anything', async () => {
    const save = vi.fn(async () => ({ trusted_proxies: [], audit_retention_days: 366 }));
    render(
      <TrustedProxiesSettings
        load={async () => ({ trusted_proxies: ['10.0.0.0/8'], audit_retention_days: 366 })}
        save={save}
      />
    );
    const textarea = await screen.findByRole('textbox');
    expect(textarea).toHaveValue('10.0.0.0/8');
    fireEvent.change(textarea, { target: { value: '10.0.0.0/8\n0.0.0.0/0' } });
    expect(screen.getByRole('alert')).toHaveTextContent('0.0.0.0/0');
    expect(saveButton()).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });

  it('warns about a network or a Docker gateway without blocking the save', async () => {
    render(
      <TrustedProxiesSettings
        load={async () => ({ trusted_proxies: [], audit_retention_days: 366 })}
      />
    );
    const textarea = await screen.findByRole('textbox');
    fireEvent.change(textarea, { target: { value: '172.20.0.10' } });
    expect(screen.queryByTestId('trusted-proxies-risk')).toBeNull();
    fireEvent.change(textarea, { target: { value: '172.18.0.0/16\n172.17.0.1' } });
    expect(screen.getByTestId('trusted-proxies-risk')).toHaveTextContent(
      '172.18.0.0/16, 172.17.0.1'
    );
    expect(saveButton()).toBeEnabled();
  });

  it('reports a refused save', async () => {
    const save = vi.fn(async (): Promise<AuthSettings> => {
      throw new Error('nope');
    });
    render(
      <TrustedProxiesSettings
        load={async () => ({ trusted_proxies: [], audit_retention_days: 366 })}
        save={save}
      />
    );
    fireEvent.change(await screen.findByRole('textbox'), { target: { value: '::1' } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalled());
  });
});

describe('TrustedProxiesSettings behind an untrusted https proxy', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    [false, true],
    [true, false],
  ])('server secure=%s shows the hint: %s', async (secure, shown) => {
    vi.stubGlobal('location', { ...window.location, protocol: 'https:' });
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              authenticated: true,
              setup_required: false,
              setup_code_required: false,
              methods: { password: true, ingress: false },
              auth_compat: { basic: false },
              secure,
              user: { id: 1, username: 'owner', display_name: 'Olive', role: 'admin' },
              via: 'lan',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          )
      )
    );
    render(
      <SessionProvider>
        <TrustedProxiesSettings
          load={async () => ({ trusted_proxies: [], audit_retention_days: 366 })}
        />
      </SessionProvider>
    );
    await screen.findByRole('textbox');
    const hint = screen.queryByText(i18n.t('settings.trustedProxies.httpsNotTrusted'));
    expect(hint !== null).toBe(shown);
  });
});
