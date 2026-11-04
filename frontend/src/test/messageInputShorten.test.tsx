import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageInput } from '../components/MessageInput';
import i18n from '../i18n';
import { toast } from '../components/ui/sonner';
import { api, ApiError } from '../api';
import { resetShareStatusStoreForTests, setShareStatus } from '../stores/shareStatusStore';
import type { ShareShortenMode, ShareStatus } from '../types';

vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockToast = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

const LONG = 'https://example.org/some/very/long/path/to/an/article?id=123456789&ref=newsletter';
const SHORT = 'https://meshl.ink/AbCdEfG';
const OVER = `${LONG} and some more words to go over the one hundred and fifty six byte limit of a direct message`;

function status(enabled: boolean, mode: ShareShortenMode): ShareStatus {
  return {
    enabled,
    host: 'meshl.ink',
    hosts: ['meshl.ink'],
    community_enabled: true,
    identity_ready: true,
    dm_encryption_available: true,
    banned: false,
    unreachable: false,
    shorten_mode: mode,
    preview_policy: 'always',
  };
}

describe('MessageInput link shortening', () => {
  const onSend = vi.fn().mockResolvedValue(undefined);
  let shortenLink: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    resetShareStatusStoreForTests();
    shortenLink = vi
      .spyOn(api, 'shortenLink')
      .mockResolvedValue({ id: 'AbCdEfG', url: SHORT, expires_at: 0 });
    vi.spyOn(api, 'getShareStatus').mockRejectedValue(new Error('offline'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function setup(
    enabled = true,
    mode: ShareShortenMode = 'auto',
    patch: Partial<ShareStatus> = {}
  ) {
    setShareStatus({ ...status(enabled, mode), ...patch });
    render(<MessageInput onSend={onSend} disabled={false} conversationType="contact" />);
    const input = document.querySelector(
      'textarea[name="chat-message-input"]'
    ) as HTMLTextAreaElement;
    return input;
  }

  const sendButton = () =>
    screen.getByRole('button', { name: i18n.t('chat.send') }) as HTMLButtonElement;

  it('auto: Send shortens a long URL silently and sends the short link', async () => {
    const input = setup();
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(SHORT));
    expect(shortenLink).toHaveBeenCalledTimes(1);
    expect(shortenLink).toHaveBeenCalledWith(LONG);
    expect(mockToast.success).not.toHaveBeenCalled();
    await waitFor(() => expect(input.value).toBe(''));
  });

  it('auto: the limit counts a long URL as the short link it becomes', async () => {
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    expect(sendButton()).toBeEnabled();
    expect(screen.queryByText(i18n.t('chat.tooLong'), { exact: false })).toBeNull();
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(OVER.replace(LONG, SHORT)));
  });

  it('auto: too long even with short links, Send stays disabled and nothing is requested', () => {
    const input = setup();
    fireEvent.change(input, { target: { value: `${LONG} ${'word '.repeat(30)}x` } });
    expect(sendButton()).toBeDisabled();
    fireEvent.click(sendButton());
    expect(shortenLink).not.toHaveBeenCalled();
  });

  it('auto: 26 bytes stay as typed, 27 are shortened', async () => {
    const atThreshold = `https://a.io/${'a'.repeat(13)}`;
    const over = `https://a.io/${'a'.repeat(14)}`;
    expect(atThreshold).toHaveLength(26);
    const input = setup();
    fireEvent.change(input, { target: { value: atThreshold } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(atThreshold));
    expect(shortenLink).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: over } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(SHORT));
    expect(shortenLink).toHaveBeenCalledWith(over);
  });

  it('auto: shortens every distinct URL in order, keeping a reply prefix', async () => {
    const other = 'https://other.org/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    shortenLink.mockResolvedValueOnce({
      id: 'AAAAAAA',
      url: 'https://meshl.ink/AAAAAAA',
      expires_at: 0,
    });
    shortenLink.mockResolvedValueOnce({
      id: 'BBBBBBB',
      url: 'https://meshl.ink/BBBBBBB',
      expires_at: 0,
    });
    const input = setup();
    fireEvent.change(input, { target: { value: `@[Ann] ${LONG} ${other} ${LONG}` } });
    fireEvent.click(sendButton());
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        '@[Ann] https://meshl.ink/AAAAAAA https://meshl.ink/BBBBBBB https://meshl.ink/AAAAAAA'
      )
    );
    expect(shortenLink.mock.calls.map((c: unknown[]) => c[0])).toEqual([LONG, other]);
  });

  it.each([429, 502])('auto: on a %i a message that fits goes out as typed', async (code) => {
    shortenLink.mockRejectedValue(new ApiError(`HTTP ${code}`, code, { detail: 'x' }));
    const input = setup();
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(LONG));
    expect(mockToast.error).not.toHaveBeenCalled();
  });

  it('auto: a service that does not answer in time does not hold the message', async () => {
    shortenLink.mockImplementation(() => new Promise(() => {}));
    const input = setup();
    vi.useFakeTimers();
    try {
      fireEvent.change(input, { target: { value: LONG } });
      fireEvent.click(sendButton());
      await vi.advanceTimersByTimeAsync(3999);
      expect(onSend).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      expect(onSend).toHaveBeenCalledWith(LONG);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ['unreachable', { unreachable: true }],
    ['banned', { banned: true }],
  ])('auto: %s service, links go out as typed without asking', async (_name, patch) => {
    const input = setup(true, 'auto', patch);
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(LONG));
    expect(shortenLink).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: OVER } });
    expect(sendButton()).toBeDisabled();
  });

  it.each([429, 502])('auto: on a %i a message too long as typed stays', async (code) => {
    shortenLink.mockRejectedValue(new ApiError(`HTTP ${code}`, code, { detail: 'x' }));
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(input.value).toBe(OVER);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('never leaves a partially rewritten text when the second URL fails', async () => {
    const other = 'https://other.org/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    shortenLink.mockResolvedValueOnce({
      id: 'AAAAAAA',
      url: 'https://meshl.ink/AAAAAAA',
      expires_at: 0,
    });
    shortenLink.mockRejectedValueOnce(new ApiError('HTTP 502', 502, { detail: 'x' }));
    const input = setup();
    const text = `${LONG} ${other}`;
    fireEvent.change(input, { target: { value: text } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(input.value).toBe(text);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('refuses a service answer that is not a share link', async () => {
    shortenLink.mockResolvedValue({ id: 'x', url: 'https://evil.example/AbCdEfG', expires_at: 0 });
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(input.value).toBe(OVER);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('a short link longer than announced: nothing sent, the text stays with Undo', async () => {
    const longShort = `https://meshl.ink/${'A'.repeat(90)}`;
    shortenLink.mockResolvedValue({ id: 'x', url: longShort, expires_at: 0 });
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(input.value).toBe(OVER.replace(LONG, longShort)));
    expect(onSend).not.toHaveBeenCalled();

    mockToast.success.mock.calls[0][1].action.onClick();
    await waitFor(() => expect(input.value).toBe(OVER));
  });

  it('a radio failure keeps the shortened text, so a retry asks for no second link', async () => {
    onSend.mockRejectedValueOnce(new Error('radio'));
    const input = setup();
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(input.value).toBe(SHORT);
    expect(shortenLink).toHaveBeenCalledTimes(1);
  });

  it('auto: text edited while shortening is not sent nor overwritten', async () => {
    let release: (v: { id: string; url: string; expires_at: number }) => void = () => {};
    shortenLink.mockImplementationOnce(
      () => new Promise((resolve) => (release = resolve as typeof release))
    );
    const input = setup();
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(shortenLink).toHaveBeenCalled());
    fireEvent.change(input, { target: { value: `${LONG} more` } });
    release({ id: 'AbCdEfG', url: SHORT, expires_at: 0 });
    await waitFor(() => expect(sendButton()).toBeEnabled());
    expect(onSend).not.toHaveBeenCalled();
    expect(input.value).toBe(`${LONG} more`);
  });

  it('a share-host URL is never shortened', async () => {
    const input = setup();
    fireEvent.change(input, { target: { value: `${SHORT}/something/long/enough/for/the/limit` } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(shortenLink).not.toHaveBeenCalled();
  });

  it('mode never: under-limit text is sent unchanged, over the limit Send is disabled', async () => {
    const input = setup(true, 'never');
    fireEvent.change(input, { target: { value: OVER } });
    expect(sendButton()).toBeDisabled();
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(LONG));
    expect(shortenLink).not.toHaveBeenCalled();
  });

  it('sharing off: no request, Send stays disabled over the limit', () => {
    const input = setup(false, 'auto');
    fireEvent.change(input, { target: { value: OVER } });
    expect(sendButton()).toBeDisabled();
    expect(shortenLink).not.toHaveBeenCalled();
  });

  it('mode always: Send shortens, then sends the shortened text without Undo', async () => {
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: OVER } });
    expect(sendButton()).toBeEnabled();
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(OVER.replace(LONG, SHORT)));
    expect(mockToast.success).toHaveBeenCalledWith(expect.stringContaining('→'), undefined);
  });

  it('mode always: the size guard judges the shortened text before any request', () => {
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: `${LONG} ${'word '.repeat(30)}x` } });
    expect(sendButton()).toBeDisabled();
    fireEvent.click(sendButton());
    expect(shortenLink).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
  });

  it('mode always: a service error sends nothing and keeps the text', async () => {
    shortenLink.mockRejectedValue(new ApiError('HTTP 502', 502, { detail: 'x' }));
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(input.value).toBe(LONG);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('a 403 share_banned shows the coded ban message and keeps the text', async () => {
    shortenLink.mockRejectedValue(
      new ApiError('HTTP 403', 403, { code: 'share_banned', params: {} })
    );
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(sendButton());
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(i18n.t('chat.shortenFailed'), {
        description: i18n.t('errors.share_banned'),
      })
    );
    expect(i18n.t('errors.share_banned')).toContain('ban@meshloom.app');
    expect(input.value).toBe(OVER);
    expect(onSend).not.toHaveBeenCalled();
  });

  const LAN = 'http://192.168.1.10:8123/dashboard/overview/with/a/long/path/segment/x';

  it('a LAN URL never blocks a send in always mode', async () => {
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: `see ${LAN}` } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(`see ${LAN}`));
    expect(shortenLink).not.toHaveBeenCalled();
  });

  it('always: shortens the public URL and sends the LAN one unchanged', async () => {
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: `${LAN} ${LONG}` } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(`${LAN} ${SHORT}`));
    expect(shortenLink).toHaveBeenCalledTimes(1);
  });

  it('auto: a LAN URL counts as typed and is never sent to the service', async () => {
    const input = setup();
    fireEvent.change(input, { target: { value: `${LAN} ${'word '.repeat(30)}` } });
    expect(sendButton()).toBeDisabled();
    fireEvent.change(input, { target: { value: `${LAN} ${LONG}` } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(`${LAN} ${SHORT}`));
    expect(shortenLink).toHaveBeenCalledTimes(1);
  });

  it('always: text edited while shortening is not sent nor overwritten', async () => {
    let release: (v: { id: string; url: string; expires_at: number }) => void = () => {};
    shortenLink.mockImplementationOnce(
      () => new Promise((resolve) => (release = resolve as typeof release))
    );
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(shortenLink).toHaveBeenCalled());
    fireEvent.change(input, { target: { value: `${LONG} more` } });
    release({ id: 'AbCdEfG', url: SHORT, expires_at: 0 });
    await waitFor(() => expect(sendButton()).toBeEnabled());
    expect(onSend).not.toHaveBeenCalled();
    expect(input.value).toBe(`${LONG} more`);
  });
});
