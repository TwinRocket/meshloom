import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageInput } from '../components/MessageInput';
import { api, ApiError, formatApiError } from '../api';
import i18n from '../i18n';
import { toast } from '../components/ui/sonner';
import { resetShareStatusStoreForTests, setShareStatus } from '../stores/shareStatusStore';
import type { ShareFile, ShareStatus } from '../types';
import * as downscale from '../utils/imageDownscale';

vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockToast = toast as unknown as { error: ReturnType<typeof vi.fn> };

function status(enabled: boolean): ShareStatus {
  return {
    enabled,
    host: 'meshl.ink',
    hosts: ['meshl.ink'],
    identity_ready: true,
    dm_encryption_available: true,
    banned: false,
    unreachable: false,
    shorten_mode: 'never',
    preview_policy: 'private',
  };
}

const STORED: ShareFile = {
  id: 'AbCdEfG',
  url: 'https://meshl.ink/AbCdEfG',
  expires_at: 0,
  size: 1000,
  mode: 'public',
  text: '\u{1F4F7} https://meshl.ink/AbCdEfG',
};

const prepared: downscale.PreparedImage = {
  blob: new Blob([new Uint8Array(10)], { type: 'image/jpeg' }),
  type: 'image/jpeg',
  original: { width: 4000, height: 3000 },
  output: { width: 1600, height: 1200 },
  originalBytes: 5_000_000,
  bytes: 200_000,
};

describe('Composer File tab', () => {
  const onSend = vi.fn().mockResolvedValue(undefined);
  let upload: ReturnType<typeof vi.spyOn>;
  let prepare: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    resetShareStatusStoreForTests();
    upload = vi.spyOn(api, 'uploadShareFile').mockResolvedValue(STORED);
    prepare = vi.spyOn(downscale, 'downscaleImage').mockResolvedValue(prepared);
    vi.spyOn(api, 'getShareStatus').mockRejectedValue(new Error('offline'));
  });
  afterEach(() => vi.restoreAllMocks());

  function setup(enabled = true) {
    setShareStatus(status(enabled));
    render(<MessageInput onSend={onSend} disabled={false} conversationType="contact" />);
    fireEvent.click(screen.getByTestId('composer-attach-trigger'));
    fireEvent.click(screen.getByTestId('composer-attach-tab-file'));
  }

  const pick = (file: File) =>
    fireEvent.change(screen.getByTestId('file-tab-input'), { target: { files: [file] } });
  const jpeg = () => new File([new Uint8Array(10)], 'photo.jpg', { type: 'image/jpeg' });

  it('shows the consent entry point when sharing is off', () => {
    setup(false);
    expect(screen.getByTestId('file-tab-enable')).toBeInTheDocument();
    expect(screen.queryByTestId('file-tab-input')).toBeNull();
    fireEvent.click(screen.getByTestId('file-tab-enable'));
    expect(screen.getByTestId('share-consent-dialog')).toBeInTheDocument();
  });

  it('says a HEIC file is unsupported and uploads nothing', async () => {
    setup();
    pick(new File([new Uint8Array(10)], 'IMG_1.heic', { type: 'image/heic' }));
    expect(await screen.findByTestId('file-tab-refusal')).toHaveTextContent(
      i18n.t('chat.fileTab.error.heic')
    );
    expect(upload).not.toHaveBeenCalled();
    expect(prepare).not.toHaveBeenCalled();
  });

  it('uploads once, then sends the suggested text with the caption', async () => {
    setup();
    pick(jpeg());
    expect(await screen.findByTestId('file-tab-summary')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('file-tab-caption'), { target: { value: 'Summit' } });
    fireEvent.click(screen.getByTestId('file-tab-send'));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(`${STORED.text} Summit`));
    expect(upload).toHaveBeenCalledTimes(1);
    expect(upload.mock.calls[0][0]).toBe(prepared.blob);
  });

  it('refuses an over-budget caption instead of truncating it', async () => {
    setup();
    pick(jpeg());
    await screen.findByTestId('file-tab-summary');
    fireEvent.change(screen.getByTestId('file-tab-caption'), {
      target: { value: 'x'.repeat(140) },
    });
    expect(screen.getByTestId('file-tab-send')).toBeDisabled();
    expect(screen.getByTestId('file-tab-budget')).toHaveTextContent(
      i18n.t('chat.fileTab.captionTooLong')
    );
    expect(upload).not.toHaveBeenCalled();
  });

  it('cancel aborts the request and leaves the tab ready', async () => {
    let signal: AbortSignal | undefined;
    upload.mockImplementation(
      (_blob: Blob, options?: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal = options?.signal;
          signal?.addEventListener('abort', () =>
            reject(new DOMException('cancelled', 'AbortError'))
          );
        })
    );
    setup();
    pick(jpeg());
    await screen.findByTestId('file-tab-summary');
    fireEvent.click(screen.getByTestId('file-tab-send'));
    fireEvent.click(await screen.findByTestId('file-tab-cancel'));
    await waitFor(() => expect(signal?.aborted).toBe(true));
    await waitFor(() => expect(screen.getByTestId('file-tab-send')).toBeEnabled());
    expect(screen.queryByTestId('file-tab-progress')).toBeNull();
    expect(onSend).not.toHaveBeenCalled();
    expect(mockToast.error).not.toHaveBeenCalled();
  });

  it('shows a coded toast when the upload is refused', async () => {
    upload.mockRejectedValue(
      new ApiError('busy', 429, { code: 'share_rate_limited', params: {} }, 5)
    );
    setup();
    pick(jpeg());
    await screen.findByTestId('file-tab-summary');
    fireEvent.click(screen.getByTestId('file-tab-send'));
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(i18n.t('errors.share_rate_limited'))
    );
    expect(onSend).not.toHaveBeenCalled();
  });

  it('opens the tab with a pasted image and none of its text lands in the textarea', async () => {
    setShareStatus(status(true));
    render(<MessageInput onSend={onSend} disabled={false} conversationType="contact" />);
    const textarea = document.querySelector('textarea[name="chat-message-input"]')!;
    const file = jpeg();
    await act(async () => {
      fireEvent.paste(textarea, { clipboardData: { files: [file], getData: () => '' } });
    });
    expect(await screen.findByTestId('file-tab-summary')).toBeInTheDocument();
    expect(prepare).toHaveBeenCalledWith(file);
  });

  it('keeps the reply prefix in the sent message', async () => {
    setShareStatus(status(true));
    const ref = { current: null as null | { startReply: (s: string, q: string) => void } };
    render(
      <MessageInput
        ref={ref as never}
        onSend={onSend}
        disabled={false}
        conversationType="contact"
      />
    );
    act(() => ref.current!.startReply('Ann', 'hi'));
    fireEvent.click(screen.getByTestId('composer-attach-trigger'));
    fireEvent.click(screen.getByTestId('composer-attach-tab-file'));
    pick(jpeg());
    await screen.findByTestId('file-tab-summary');
    fireEvent.click(screen.getByTestId('file-tab-send'));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(`@[Ann] ${STORED.text}`));
  });
});

describe('share error messages', () => {
  const CODES = [
    'share_disabled',
    'share_unavailable',
    'share_quota_exceeded',
    'share_quota_key',
    'share_quota_ip',
    'share_quota_global',
    'share_rate_limited',
    'share_banned',
    'share_too_large',
    'share_type_refused',
    'share_invalid_request',
    'share_cross_site',
    'share_mode_unavailable',
  ];

  it.each(['en', 'fr'])('translates every upload error code in %s', async (lng) => {
    await i18n.changeLanguage(lng);
    try {
      for (const code of CODES) {
        const err = new ApiError('raw', 400, { code, params: {} });
        expect(formatApiError(err, i18n.t.bind(i18n))).not.toBe('raw');
        expect(i18n.exists(`errors.${code}`)).toBe(true);
      }
    } finally {
      await i18n.changeLanguage('fr');
    }
  });
});
