import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));

import {
  TrustedProxiesSettings,
  isProxyEntry,
} from '../components/settings/TrustedProxiesSettings';
import type { AuthSettings } from '../auth/types';
import i18n from '../i18n';

const saveButton = () =>
  screen.getByRole('button', { name: i18n.t('settings.trustedProxies.save') });

describe('isProxyEntry', () => {
  it.each(['127.0.0.1', ' 10.0.0.0/8 ', '172.18.0.0/16', '::1', 'fd00::/8', '2001:db8::1/128'])(
    'accepts %s',
    (raw) => {
      expect(isProxyEntry(raw)).toBe(true);
    }
  );

  it.each([
    '0.0.0.0/0',
    '::/0',
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
    const load = vi.fn(async () => ({ trusted_proxies: [] }));
    const save = vi.fn(async () => ({ trusted_proxies: ['127.0.0.1/32'] }));
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
    const save = vi.fn(async () => ({ trusted_proxies: [] }));
    render(
      <TrustedProxiesSettings
        load={async () => ({ trusted_proxies: ['10.0.0.0/8'] })}
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

  it('reports a refused save', async () => {
    const save = vi.fn(async (): Promise<AuthSettings> => {
      throw new Error('nope');
    });
    render(<TrustedProxiesSettings load={async () => ({ trusted_proxies: [] })} save={save} />);
    fireEvent.change(await screen.findByRole('textbox'), { target: { value: '::1' } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalled());
  });
});
