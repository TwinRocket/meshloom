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
    identity_ready: true,
    dm_encryption_available: true,
    banned: false,
    unreachable: false,
    shorten_mode: mode,
    preview_policy: 'private',
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

  function setup(enabled = true, mode: ShareShortenMode = 'auto') {
    setShareStatus(status(enabled, mode));
    render(<MessageInput onSend={onSend} disabled={false} conversationType="contact" />);
    const input = document.querySelector(
      'textarea[name="chat-message-input"]'
    ) as HTMLTextAreaElement;
    return input;
  }

  const sendButton = () =>
    screen.getByRole('button', { name: i18n.t('chat.send') }) as HTMLButtonElement;

  it('shows Shorten over the limit; the click rewrites the textarea and sends nothing', async () => {
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    expect(sendButton()).toBeDisabled();
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);

    await waitFor(() => expect(input.value).toBe(OVER.replace(LONG, SHORT)));
    expect(shortenLink).toHaveBeenCalledTimes(1);
    expect(shortenLink).toHaveBeenCalledWith(LONG);
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
    expect(sendButton()).toBeEnabled();
    expect(mockToast.success).toHaveBeenCalledWith(
      expect.stringContaining('→'),
      expect.objectContaining({
        action: expect.objectContaining({ onClick: expect.any(Function) }),
      })
    );
  });

  it('Undo restores the original text and Send is disabled again', async () => {
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);
    await waitFor(() => expect(input.value).toContain(SHORT));

    mockToast.success.mock.calls[0][1].action.onClick();
    await waitFor(() => expect(input.value).toBe(OVER));
    expect(sendButton()).toBeDisabled();
  });

  it('shortens every distinct URL in order, keeping a reply prefix', async () => {
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
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);
    await waitFor(() =>
      expect(input.value).toBe(
        '@[Ann] https://meshl.ink/AAAAAAA https://meshl.ink/BBBBBBB https://meshl.ink/AAAAAAA'
      )
    );
    expect(shortenLink.mock.calls.map((c: unknown[]) => c[0])).toEqual([LONG, other]);
  });

  it.each([429, 502])('keeps the original text and sends nothing on a %i', async (code) => {
    shortenLink.mockRejectedValue(new ApiError(`HTTP ${code}`, code, { detail: 'x' }));
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);
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
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(input.value).toBe(text);
  });

  it('refuses a service answer that is not a share link', async () => {
    shortenLink.mockResolvedValue({ id: 'x', url: 'https://evil.example/AbCdEfG', expires_at: 0 });
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(input.value).toBe(OVER);
  });

  it('shows no button under the limit, without a long URL, or for a service link', () => {
    const input = setup();
    fireEvent.change(input, { target: { value: LONG } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
    fireEvent.change(input, { target: { value: 'word '.repeat(40) } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
    fireEvent.change(input, { target: { value: `${SHORT} ${'word '.repeat(40)}` } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
  });

  it('mode never: no button, and under-limit text is sent unchanged', async () => {
    const input = setup(true, 'never');
    fireEvent.change(input, { target: { value: OVER } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
    expect(sendButton()).toBeDisabled();
    fireEvent.change(input, { target: { value: LONG } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(LONG));
    expect(shortenLink).not.toHaveBeenCalled();
  });

  it('sharing off: no button, no request, Send stays disabled over the limit', () => {
    const input = setup(false, 'auto');
    fireEvent.change(input, { target: { value: OVER } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
    expect(sendButton()).toBeDisabled();
    expect(shortenLink).not.toHaveBeenCalled();
  });

  it('mode always: Send shortens, then sends the shortened text without Undo', async () => {
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: OVER } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
    expect(sendButton()).toBeEnabled();
    fireEvent.click(sendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(OVER.replace(LONG, SHORT)));
    expect(mockToast.success).toHaveBeenCalledWith(expect.stringContaining('→'), undefined);
  });

  it('mode always: the size guard still judges the shortened text', async () => {
    const input = setup(true, 'always');
    const text = `${LONG} ${'word '.repeat(30)}`;
    fireEvent.change(input, { target: { value: text } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(input.value).toBe(text.replace(LONG, SHORT).trim()));
    expect(onSend).not.toHaveBeenCalled();
    expect(sendButton()).toBeDisabled();
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
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);
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

  it('a LAN URL never shows the button nor blocks a send in always mode', async () => {
    const input = setup(true, 'always');
    fireEvent.change(input, { target: { value: `see ${LAN}` } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
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

  it('auto: no button when the only long URL is a LAN one', () => {
    const input = setup();
    fireEvent.change(input, { target: { value: `${LAN} ${'word '.repeat(30)}` } });
    expect(screen.queryAllByTestId('composer-shorten')).toHaveLength(0);
  });

  it('keeps text typed while shortening, and marks the button busy', async () => {
    let release: (v: { id: string; url: string; expires_at: number }) => void = () => {};
    shortenLink.mockImplementationOnce(
      () => new Promise((resolve) => (release = resolve as typeof release))
    );
    const input = setup();
    fireEvent.change(input, { target: { value: OVER } });
    fireEvent.click(screen.getAllByTestId('composer-shorten')[0]);
    await waitFor(() =>
      expect(screen.getAllByTestId('composer-shorten')[0]).toHaveAttribute('aria-busy', 'true')
    );
    fireEvent.change(input, { target: { value: `${OVER} typed meanwhile` } });
    release({ id: 'AbCdEfG', url: SHORT, expires_at: 0 });
    await waitFor(() =>
      expect(screen.getAllByTestId('composer-shorten')[0]).toHaveAttribute('aria-busy', 'false')
    );
    expect(input.value).toBe(`${OVER} typed meanwhile`);
    expect(mockToast.success).not.toHaveBeenCalled();
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
