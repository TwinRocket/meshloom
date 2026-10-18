import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api } from '../api';
import { MessageBody } from '../components/message/MessageBody';
import {
  __resetShareMetaStoreForTests,
  META_MAX_ENTRIES,
  META_TTL_MS,
  getShareMetaEntry,
  requestShareMeta,
} from '../stores/shareMetaStore';
import type { ShareStatus } from '../types';
import { describeMessagePreview } from '../utils/messagePreview';
import { displayShareHost, parseShareLink, splitShareLinkBody } from '../utils/shareLinks';
import i18n from '../i18n';

const URL_A = 'https://meshl.ink/AbCdEfG';

function status(enabled: boolean): ShareStatus {
  return {
    enabled,
    host: 'meshl.ink',
    hosts: ['meshl.ink'],
    identity_ready: true,
    dm_encryption_available: true,
    banned: false,
    unreachable: false,
    shorten_mode: 'auto',
    preview_policy: 'private',
  };
}

describe('shareLinks grammar', () => {
  it('accepts a service link and rejects look-alikes', () => {
    expect(parseShareLink(URL_A)).toBe('AbCdEfG');
    expect(parseShareLink('https://MESHL.INK/l/AbCdEfG')).toBe('AbCdEfG');
    expect(parseShareLink('http://meshl.ink/AbCdEfG')).toBeNull();
    expect(parseShareLink('https://meshl.ink.evil.org/AbCdEfG')).toBeNull();
    expect(parseShareLink('https://meshl.ink/AbCdEf0')).toBeNull(); // 0 not base58
    expect(parseShareLink('https://meshl.ink/AbCdEfGh')).toBeNull();
    expect(parseShareLink('https://user@meshl.ink/AbCdEfG')).toBeNull();
  });

  it('splits a caption off', () => {
    expect(splitShareLinkBody(`${URL_A} sunset`)).toEqual({ id: 'AbCdEfG', caption: 'sunset' });
    expect(splitShareLinkBody(`look ${URL_A}`)).toBeNull();
  });
});

describe('displayShareHost', () => {
  it('strips bidi and control characters', () => {
    expect(displayShareHost('exa\u202Emple.org')?.text).toBe('example.org');
    expect(displayShareHost('\u202E\u2066')).toBeNull();
  });
  it('shows an IDN host as punycode', () => {
    expect(displayShareHost('exаmple.org')?.text).toMatch(/^xn--/);
  });
  it('cuts a very long value and keeps the whole one in full', () => {
    const out = displayShareHost('a'.repeat(5000));
    expect(out?.text.length).toBe(254);
    expect(out?.text.endsWith('…')).toBe(true);
    expect(out?.full.length).toBe(5000);
  });
});

describe('ShareLinkAnchor', () => {
  let metaSpy: ReturnType<typeof vi.spyOn>;
  let statusSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    __resetShareMetaStoreForTests();
    metaSpy = vi
      .spyOn(api, 'getShareMeta')
      .mockResolvedValue({ kind: 'link', target_host: 'example.org', expires_at: null });
    statusSpy = vi.spyOn(api, 'getShareStatus').mockResolvedValue(status(true));
  });
  afterEach(() => vi.restoreAllMocks());

  it('makes no meta request on render, one on hover, none on the second', async () => {
    render(<MessageBody content={`${URL_A} and ${URL_A}`} />);
    await waitFor(() => expect(statusSpy).toHaveBeenCalled());
    expect(metaSpy).not.toHaveBeenCalled();

    const links = screen.getAllByRole('link');
    fireEvent.mouseEnter(links[0]);
    await waitFor(() => expect(screen.getAllByTestId('share-link-hint').length).toBe(2));
    expect(metaSpy).toHaveBeenCalledTimes(1);
    expect(screen.getAllByTestId('share-link-hint')[0].textContent).toBe('→ example.org');

    fireEvent.mouseEnter(links[0]);
    fireEvent.focus(links[1]);
    await act(async () => {});
    expect(metaSpy).toHaveBeenCalledTimes(1);
  });

  it('shows no hint and fetches no meta when sharing is off', async () => {
    statusSpy.mockResolvedValue(status(false));
    render(<MessageBody content={URL_A} />);
    await waitFor(() => expect(statusSpy).toHaveBeenCalled());
    fireEvent.mouseEnter(screen.getByRole('link'));
    fireEvent.focus(screen.getByRole('link'));
    await act(async () => {});
    expect(metaSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('share-link-hint')).toBeNull();
  });

  it('does not even read the status for an ordinary link', async () => {
    render(<MessageBody content="see https://example.org/page" />);
    await act(async () => {});
    expect(statusSpy).not.toHaveBeenCalled();
    expect(metaSpy).not.toHaveBeenCalled();
  });
});

describe('ShareLinkAnchor hostile host', () => {
  it('renders the remote host isolated, sanitised and truncated', async () => {
    __resetShareMetaStoreForTests();
    vi.spyOn(api, 'getShareStatus').mockResolvedValue(status(true));
    vi.spyOn(api, 'getShareMeta').mockResolvedValue({
      kind: 'link',
      target_host: `\u202Eevil${'a'.repeat(5000)}.org`,
      expires_at: null,
    });
    render(<MessageBody content={URL_A} />);
    await waitFor(() => expect(api.getShareStatus).toHaveBeenCalled());
    fireEvent.mouseEnter(screen.getByRole('link'));
    const hint = await screen.findByTestId('share-link-hint');
    expect(hint.querySelector('bdi')?.getAttribute('dir')).toBe('ltr');
    expect(hint.textContent).not.toMatch(/[\u202E]/);
    expect(hint.textContent!.length).toBeLessThan(270);
    expect(hint.getAttribute('title')!.length).toBeGreaterThan(5000);
    expect(screen.getByRole('link').getAttribute('aria-describedby')).toBe(hint.id);
    expect(screen.getByRole('link').getAttribute('title')).not.toMatch(/\u202E/);
    vi.restoreAllMocks();
  });
});

describe('shareMetaStore', () => {
  beforeEach(() => {
    __resetShareMetaStoreForTests();
    vi.spyOn(api, 'getShareStatus').mockResolvedValue(status(true));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('keeps a negative entry and refetches after ten minutes', async () => {
    const metaSpy = vi.spyOn(api, 'getShareMeta').mockRejectedValue(new Error('404'));
    await requestShareMeta('AbCdEfG');
    await requestShareMeta('AbCdEfG');
    expect(metaSpy).toHaveBeenCalledTimes(1);
    expect(getShareMetaEntry('AbCdEfG')?.state).toBe('missing');

    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.now() + META_TTL_MS + 1);
    metaSpy.mockResolvedValue({ kind: 'link', target_host: null, expires_at: null });
    await requestShareMeta('AbCdEfG');
    expect(metaSpy).toHaveBeenCalledTimes(2);
  });

  it('keeps at most META_MAX_ENTRIES ids, dropping the oldest', async () => {
    vi.spyOn(api, 'getShareMeta').mockResolvedValue({
      kind: 'link',
      target_host: null,
      expires_at: null,
    });
    for (let i = 0; i < META_MAX_ENTRIES + 5; i++) await requestShareMeta(`id${i}`);
    expect(getShareMetaEntry('id0')).toBeNull();
    expect(getShareMetaEntry(`id${META_MAX_ENTRIES + 4}`)?.state).toBe('ready');
  });

  it('shares one request between concurrent calls', async () => {
    const metaSpy = vi
      .spyOn(api, 'getShareMeta')
      .mockResolvedValue({ kind: 'link', target_host: 'a.org', expires_at: null });
    await Promise.all([requestShareMeta('AbCdEfG'), requestShareMeta('AbCdEfG')]);
    expect(metaSpy).toHaveBeenCalledTimes(1);
  });
});

describe('service link preview', () => {
  it('previews a bare link and a captioned one', () => {
    expect(describeMessagePreview(URL_A)).toBe(`🔗 ${i18n.t('messagePreview.shareLink')}`);
    expect(describeMessagePreview(`${URL_A} sunset`)).toBe('🔗 sunset');
    expect(describeMessagePreview(`Alice: ${URL_A} sunset`)).toBe('Alice: 🔗 sunset');
  });
  it('leaves a sentence containing a link alone', () => {
    const text = `look at ${URL_A}`;
    expect(describeMessagePreview(text)).toBe(text);
  });
});
