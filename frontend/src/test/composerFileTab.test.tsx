import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageInput } from '../components/MessageInput';
import { api, ApiError, formatApiError } from '../api';
import i18n from '../i18n';
import { toast } from '../components/ui/sonner';
import { resetShareStatusStoreForTests, setShareStatus } from '../stores/shareStatusStore';
import type { ShareFile, ShareStatus } from '../types';
import * as downscale from '../utils/imageDownscale';
import type { ShareTarget } from '../utils/channelClass';
import { SHARE_MODE_STORAGE } from '../utils/shareMode';
import en from '../i18n/locales/slices/sf.en.json';
import fr from '../i18n/locales/slices/sf.fr.json';

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

describe('File tab mode', () => {
  const onSend = vi.fn().mockResolvedValue(undefined);
  let upload: ReturnType<typeof vi.spyOn>;
  const DM: ShareTarget = {
    class: 'dm',
    encryptable: true,
    conversation: { type: 'contact', id: 'ab'.repeat(32) },
  };
  const PRIVATE: ShareTarget = {
    class: 'private',
    encryptable: true,
    conversation: { type: 'channel', id: 'conv1' },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem(SHARE_MODE_STORAGE);
    resetShareStatusStoreForTests();
    upload = vi.spyOn(api, 'uploadShareFile').mockResolvedValue(STORED);
    vi.spyOn(downscale, 'downscaleImage').mockResolvedValue(prepared);
    vi.spyOn(api, 'getShareStatus').mockRejectedValue(new Error('offline'));
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.removeItem(SHARE_MODE_STORAGE);
  });

  function open(target: ShareTarget | null, over: Partial<ShareStatus> = {}) {
    setShareStatus({ ...status(true), ...over });
    render(
      <MessageInput
        onSend={onSend}
        disabled={false}
        conversationType={
          target?.class === 'dm' || target?.class === 'room' ? 'contact' : 'channel'
        }
        shareTarget={target}
      />
    );
    fireEvent.click(screen.getByTestId('composer-attach-trigger'));
    fireEvent.click(screen.getByTestId('composer-attach-tab-file'));
  }
  const checked = (mode: string) =>
    screen.getByTestId(`file-tab-mode-${mode}`).getAttribute('aria-checked');

  it.each([
    ['a private channel', PRIVATE],
    ['a DM', DM],
  ])('defaults to encrypted in %s', (_label, target) => {
    open(target);
    expect(checked('encrypted')).toBe('true');
    expect(checked('public')).toBe('false');
    expect(screen.queryByTestId('file-tab-mode-clear')).toBeNull();
  });

  it.each(['public', 'hashtag_derived', 'room'] as const)(
    'offers no encryption in a %s conversation and says why',
    (cls) => {
      open({ class: cls, encryptable: false, conversation: null });
      expect(screen.queryByTestId('file-tab-mode')).toBeNull();
      expect(screen.getByTestId('file-tab-mode-clear')).toHaveTextContent(
        i18n.t(`chat.fileTab.mode.clear.${cls}`)
      );
    }
  );

  it('never says private to explain a non-encryptable conversation', () => {
    for (const text of [
      ...Object.values(en.chat.fileTab.mode.clear),
      ...Object.values(fr.chat.fileTab.mode.clear),
    ]) {
      expect(text.toLowerCase()).not.toMatch(/priv/);
    }
  });

  it('remembers the last choice', () => {
    open(PRIVATE);
    fireEvent.click(screen.getByTestId('file-tab-mode-public'));
    expect(checked('public')).toBe('true');
    expect(localStorage.getItem(SHARE_MODE_STORAGE)).toBe('public');
    cleanup();
    open(DM);
    expect(checked('public')).toBe('true');
  });

  it('disables encryption in a DM until the radio key is read', () => {
    open(DM, { dm_encryption_available: false });
    expect(screen.getByTestId('file-tab-mode-encrypted')).toBeDisabled();
    expect(checked('public')).toBe('true');
    expect(screen.getByTestId('file-tab-mode-hint')).toHaveTextContent(
      i18n.t('chat.fileTab.mode.dmUnavailable')
    );
  });

  it('uploads sealed for the conversation, with the file name', async () => {
    open(PRIVATE);
    fireEvent.change(screen.getByTestId('file-tab-input'), {
      target: { files: [new File([new Uint8Array(10)], 'summit.jpg', { type: 'image/jpeg' })] },
    });
    await screen.findByTestId('file-tab-summary');
    fireEvent.click(screen.getByTestId('file-tab-send'));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    expect(upload.mock.calls[0][1]).toMatchObject({
      encrypted: { type: 'channel', id: 'conv1' },
      name: 'summit.jpg',
    });
  });

  it('uploads in public mode with no name when public is chosen', async () => {
    open(PRIVATE);
    fireEvent.click(screen.getByTestId('file-tab-mode-public'));
    fireEvent.change(screen.getByTestId('file-tab-input'), {
      target: { files: [new File([new Uint8Array(10)], 'summit.jpg', { type: 'image/jpeg' })] },
    });
    await screen.findByTestId('file-tab-summary');
    fireEvent.click(screen.getByTestId('file-tab-send'));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    const options = upload.mock.calls[0][1] as { encrypted?: unknown; name?: unknown };
    expect(options.encrypted).toBeUndefined();
    expect(options.name).toBeUndefined();
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
    'share_mode_not_allowed',
    'share_radio_key_unavailable',
    'share_undecryptable',
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

describe('pasted image replay', () => {
  it('does not replay a consumed paste when the File tab is closed and reopened', async () => {
    resetShareStatusStoreForTests();
    setShareStatus(status(true));
    const prepare = vi.spyOn(downscale, 'downscaleImage').mockResolvedValue(prepared);
    render(<MessageInput onSend={vi.fn()} disabled={false} conversationType="contact" />);
    const textarea = document.querySelector('textarea[name="chat-message-input"]')!;
    const file = new File([new Uint8Array(10)], 'p.jpg', { type: 'image/jpeg' });
    await act(async () => {
      fireEvent.paste(textarea, { clipboardData: { files: [file], getData: () => '' } });
    });
    await screen.findByTestId('file-tab-summary');
    expect(prepare).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText(i18n.t('chat.attachClose')));
    fireEvent.click(screen.getByTestId('composer-attach-trigger'));
    fireEvent.click(screen.getByTestId('composer-attach-tab-file'));
    expect(screen.queryByTestId('file-tab-summary')).toBeNull();
    expect(prepare).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();
  });
});
