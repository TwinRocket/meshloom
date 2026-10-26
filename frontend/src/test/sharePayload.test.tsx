import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { MessageBody } from '../components/message/MessageBody';
import type { ShareMessageContext } from '../components/message/SharePayload';
import { BlockedSendersProvider } from '../contexts/BlockedSendersContext';
import { __resetShareMetaStoreForTests, loadShareStatus } from '../stores/shareMetaStore';
import type { SharePreviewPolicy, ShareStatus } from '../types';
import {
  isPrivateChannel,
  isPrivateConversation,
  shareImageId,
  shareMediaAutoLoads,
} from '../utils/sharePayload';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';
import i18n from '../i18n';
import type { AuthRole } from '../auth/types';

const roleRef = vi.hoisted(() => ({ current: null as string | null }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('../auth/SessionProvider', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/SessionProvider')>();
  const { capabilitiesFor } = await import('../auth/capabilities');
  return {
    ...actual,
    useCapabilities: () =>
      roleRef.current ? capabilitiesFor(roleRef.current as AuthRole) : actual.useCapabilities(),
  };
});

vi.mock('../components/ui/sonner', () => ({ toast: toastMock }));

const ID = 'AbCdEfG';
const BODY = `📷 https://meshl.ink/${ID} sunset`;
const PRIVATE_KEY = '0123456789ABCDEF0123456789ABCDEF';
const HASHTAG_KEY = 'FEDCBA9876543210FEDCBA9876543210';
const CHANNELS = [
  { key: PUBLIC_CHANNEL_KEY, key_derived: false },
  { key: PRIVATE_KEY, key_derived: false },
  { key: HASHTAG_KEY, key_derived: true },
];

function status(policy: SharePreviewPolicy, enabled = true): ShareStatus {
  return {
    enabled,
    host: 'meshl.ink',
    hosts: ['meshl.ink'],
    identity_ready: true,
    dm_encryption_available: true,
    banned: false,
    unreachable: false,
    shorten_mode: 'auto',
    preview_policy: policy,
  };
}

const IMAGE_META = {
  kind: 'file',
  target_host: null,
  expires_at: null,
  mode: 'public',
  content_type: 'image/png',
  size: 1234,
  name: null,
  decryptable: true,
};

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

let fetchSpy: ReturnType<typeof vi.fn>;
let metaResponse: () => Response;

/** Every request goes through this spy; the status is answered before the test starts. */
async function setup(policy: SharePreviewPolicy) {
  __resetShareMetaStoreForTests();
  metaResponse = () => jsonResponse(IMAGE_META);
  fetchSpy = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/share/status')) return jsonResponse(status(policy));
    if (url.endsWith(`/share/${ID}/meta`)) return metaResponse();
    if (url.endsWith(`/share/${ID}/report`))
      return jsonResponse({ received: true }, { status: 202 });
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchSpy);
  await loadShareStatus(true);
  fetchSpy.mockClear();
}

function message(over: Partial<ShareMessageContext> = {}): ShareMessageContext {
  return {
    id: 42,
    type: 'CHAN',
    conversation_key: PRIVATE_KEY,
    sender_key: 'aa'.repeat(32),
    sender_name: 'Alice',
    outgoing: false,
    ...over,
  };
}

function renderBody(
  msg: ShareMessageContext | undefined,
  opts: {
    remoteMedia?: boolean;
    blockedKeys?: string[];
    blockedNames?: string[];
    loaded?: boolean;
    interactive?: boolean;
  } = {}
) {
  return render(
    <BlockedSendersProvider
      blockedKeys={opts.blockedKeys ?? []}
      blockedNames={opts.blockedNames ?? []}
      loaded={opts.loaded}
    >
      <MessageBody
        content={BODY}
        message={msg}
        privateConversation={msg ? isPrivateConversation(msg, CHANNELS) : false}
        remoteMedia={opts.remoteMedia}
        interactive={opts.interactive}
      />
    </BlockedSendersProvider>
  );
}

async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('sharePayload helpers', () => {
  it('finds the image id of the composer form only', () => {
    const hosts = ['meshl.ink'];
    expect(shareImageId(BODY, hosts)).toBe(ID);
    expect(shareImageId(`@[Bob] 📷 https://meshl.ink/${ID}`, hosts)).toBe(ID);
    expect(shareImageId(`https://meshl.ink/${ID}`, hosts)).toBeNull();
    expect(shareImageId(`📷https://meshl.ink/${ID}`, hosts)).toBeNull();
    expect(shareImageId(`📷 https://evil.org/${ID}`, hosts)).toBeNull();
  });

  it('classes channels', () => {
    expect(isPrivateChannel({ key: PRIVATE_KEY, key_derived: false })).toBe(true);
    expect(isPrivateChannel({ key: HASHTAG_KEY, key_derived: true })).toBe(false);
    expect(isPrivateChannel({ key: PUBLIC_CHANNEL_KEY, key_derived: false })).toBe(false);
    expect(isPrivateChannel({ key: PRIVATE_KEY })).toBe(false);
    expect(isPrivateChannel(undefined)).toBe(false);
    expect(isPrivateConversation({ type: 'PRIV', conversation_key: 'ab' }, [])).toBe(true);
    expect(isPrivateConversation({ type: 'CHAN', conversation_key: 'unknown' }, CHANNELS)).toBe(
      false
    );
  });

  it('applies the policy matrix', () => {
    const base = { privateConversation: true, remoteMedia: true, blocked: false };
    expect(shareMediaAutoLoads({ ...base, policy: 'always' })).toBe(true);
    expect(shareMediaAutoLoads({ ...base, policy: 'private' })).toBe(true);
    expect(shareMediaAutoLoads({ ...base, policy: 'never' })).toBe(false);
    expect(shareMediaAutoLoads({ ...base, policy: 'private', privateConversation: false })).toBe(
      false
    );
    expect(shareMediaAutoLoads({ ...base, policy: 'always', remoteMedia: false })).toBe(false);
    expect(shareMediaAutoLoads({ ...base, policy: 'always', blocked: true })).toBe(false);
  });
});

describe('SharePayload', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    roleRef.current = null;
    toastMock.success.mockReset();
    toastMock.error.mockReset();
  });

  describe('policy private', () => {
    beforeEach(() => setup('private'));

    it('auto-loads in a DM', async () => {
      renderBody(message({ type: 'PRIV', conversation_key: 'bb'.repeat(32) }));
      const img = await screen.findByTestId('share-payload-image');
      expect(img.getAttribute('src')).toBe(`./api/share/${ID}?message_id=42`);
      expect(img.getAttribute('loading')).toBe('lazy');
      // The anchor stays.
      expect(screen.getByRole('link', { name: `https://meshl.ink/${ID}` })).toBeTruthy();
    });

    it('auto-loads in a private channel', async () => {
      renderBody(message());
      expect(await screen.findByTestId('share-payload-image')).toBeTruthy();
    });

    it.each([
      ['Public', PUBLIC_CHANNEL_KEY],
      ['a derived hashtag channel', HASHTAG_KEY],
    ])('shows a button in %s and makes no request before the tap', async (_label, key) => {
      renderBody(message({ conversation_key: key }));
      await settle();
      expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
      expect(screen.queryByTestId('share-payload-image')).toBeNull();
      expect(fetchSpy).not.toHaveBeenCalled();

      fireEvent.click(screen.getByTestId('share-payload-tap'));
      expect(await screen.findByTestId('share-payload-image')).toBeTruthy();
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(String(fetchSpy.mock.calls[0][0])).toBe(`./api/share/${ID}/meta`);
    });

    it('remoteMedia={false} forces the button', async () => {
      renderBody(message({ type: 'PRIV' }), { remoteMedia: false });
      await settle();
      expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('never auto-loads from a blocked sender (key or name)', async () => {
      const { unmount } = renderBody(message({ sender_key: 'cc'.repeat(32) }), {
        blockedKeys: ['cc'.repeat(32)],
      });
      await settle();
      expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
      unmount();
      renderBody(message({ type: 'PRIV', conversation_key: 'dd'.repeat(32) }), {
        blockedNames: ['Alice'],
      });
      await settle();
      expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('without a message never auto-loads', async () => {
      renderBody(undefined);
      await settle();
      expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('says expired or removed on a 410', async () => {
      metaResponse = () =>
        jsonResponse({ detail: { code: 'share_gone', params: {} } }, { status: 410 });
      renderBody(message());
      expect(await screen.findByTestId('share-payload-gone')).toHaveTextContent(
        i18n.t('sharePayload.gone')
      );
    });

    it('falls back to the raw link on another error', async () => {
      metaResponse = () =>
        jsonResponse({ detail: { code: 'share_unavailable', params: {} } }, { status: 503 });
      renderBody(message());
      await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
      await settle();
      expect(screen.queryByTestId('share-payload')).toBeNull();
      expect(screen.queryByTestId('share-payload-gone')).toBeNull();
      expect(screen.getByRole('link', { name: `https://meshl.ink/${ID}` })).toBeTruthy();
    });

    it('falls back to the raw link when the image fails to load', async () => {
      renderBody(message());
      const img = await screen.findByTestId('share-payload-image');
      fireEvent.error(img);
      expect(screen.queryByTestId('share-payload-image')).toBeNull();
    });

    it('offers a download card above 8 MiB', async () => {
      metaResponse = () => jsonResponse({ ...IMAGE_META, size: 9 * 1024 * 1024 });
      renderBody(message());
      const card = await screen.findByTestId('share-payload-download');
      expect(card.getAttribute('href')).toBe(`./api/share/${ID}?message_id=42`);
      expect(screen.queryByTestId('share-payload-image')).toBeNull();
    });

    it('renders nothing for a link object', async () => {
      metaResponse = () =>
        jsonResponse({ kind: 'link', target_host: 'example.org', expires_at: null });
      renderBody(message());
      await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
      await settle();
      expect(screen.queryByTestId('share-payload')).toBeNull();
    });

    it('reports from the card', async () => {
      renderBody(message());
      fireEvent.click(await screen.findByTestId('share-payload-report'));
      fireEvent.change(await screen.findByTestId('share-report-reason'), {
        target: { value: 'phishing' },
      });
      fireEvent.change(screen.getByTestId('share-report-details'), {
        target: { value: ' Fake login ' },
      });
      fireEvent.click(screen.getByTestId('share-report-submit'));
      await waitFor(() =>
        expect(fetchSpy.mock.calls.some(([url]) => String(url).endsWith('/report'))).toBe(true)
      );
      const [url, init] = fetchSpy.mock.calls.find(([u]) => String(u).endsWith('/report'))!;
      expect(String(url)).toBe(`./api/share/${ID}/report`);
      expect((init as RequestInit).method).toBe('POST');
      expect(JSON.parse(String((init as RequestInit).body))).toEqual({
        reason: 'phishing',
        details: 'Fake login',
      });
      await waitFor(() => expect(screen.queryByTestId('share-report-dialog')).toBeNull());
    });
  });

  it('policy never always shows the button, even in a DM', async () => {
    await setup('never');
    renderBody(message({ type: 'PRIV' }));
    await settle();
    expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('policy always auto-loads in Public', async () => {
    await setup('always');
    renderBody(message({ conversation_key: PUBLIC_CHANNEL_KEY }));
    expect(await screen.findByTestId('share-payload-image')).toBeTruthy();
  });

  it('draws nothing with sharing off', async () => {
    await setup('always');
    __resetShareMetaStoreForTests();
    fetchSpy.mockImplementation(async () => jsonResponse(status('always', false)));
    await loadShareStatus(true);
    fetchSpy.mockClear();
    renderBody(message({ type: 'PRIV' }));
    await settle();
    expect(screen.queryByTestId('share-payload-tap')).toBeNull();
    expect(screen.queryByTestId('share-payload-image')).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  describe('the visible link does not warm the meta before the tap', () => {
    const link = () => screen.getByRole('link', { name: `https://meshl.ink/${ID}` });

    it.each([
      ['hover', (el: HTMLElement) => fireEvent.mouseEnter(el)],
      ['focus', (el: HTMLElement) => fireEvent.focus(el)],
    ])('policy never, blocked sender in a DM: %s sends nothing', async (_label, act) => {
      await setup('never');
      renderBody(message({ type: 'PRIV', conversation_key: 'bb'.repeat(32) }), {
        blockedNames: ['Alice'],
      });
      await settle();
      act(link());
      await settle();
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
    });

    it('policy always, blocked sender: hover sends nothing', async () => {
      await setup('always');
      renderBody(message({ conversation_key: PUBLIC_CHANNEL_KEY }), {
        blockedKeys: ['aa'.repeat(32)],
      });
      await settle();
      fireEvent.mouseEnter(link());
      fireEvent.focus(link());
      await settle();
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('once tapped, hover may use the meta already asked for', async () => {
      await setup('never');
      renderBody(message({ type: 'PRIV' }));
      await settle();
      fireEvent.click(screen.getByTestId('share-payload-tap'));
      expect(await screen.findByTestId('share-payload-image')).toBeTruthy();
      fireEvent.mouseEnter(link());
      await settle();
      // The store answers from its cache: still the single meta request of the tap.
      expect(fetchSpy.mock.calls.map(([u]) => String(u))).toEqual([`./api/share/${ID}/meta`]);
    });

    it('auto-load: the image and the hover share one meta request', async () => {
      await setup('always');
      renderBody(message({ conversation_key: PUBLIC_CHANNEL_KEY }));
      expect(await screen.findByTestId('share-payload-image')).toBeTruthy();
      fireEvent.mouseEnter(link());
      await settle();
      expect(fetchSpy.mock.calls.map(([u]) => String(u))).toEqual([`./api/share/${ID}/meta`]);
    });
  });

  it('policy always does not auto-load before the settings are loaded', async () => {
    await setup('always');
    renderBody(message({ conversation_key: PUBLIC_CHANNEL_KEY }), { loaded: false });
    await settle();
    expect(screen.getByTestId('share-payload-tap')).toBeTruthy();
    fireEvent.mouseEnter(screen.getByRole('link', { name: `https://meshl.ink/${ID}` }));
    await settle();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('display mode shows a plain label, never a tap button', async () => {
    await setup('never');
    renderBody(message(), { remoteMedia: false, interactive: false });
    await settle();
    expect(screen.queryByTestId('share-payload-tap')).toBeNull();
    expect(screen.getByTestId('share-payload-placeholder')).toHaveTextContent(
      i18n.t('sharePayload.notLoaded')
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each(['viewer', 'kiosk'])('hides Report for %s (no send)', async (role) => {
    await setup('always');
    roleRef.current = role;
    renderBody(message({ conversation_key: PUBLIC_CHANNEL_KEY }));
    expect(await screen.findByTestId('share-payload-image')).toBeTruthy();
    expect(screen.queryByTestId('share-payload-report')).toBeNull();
  });

  it('shows Report for a member', async () => {
    await setup('always');
    roleRef.current = 'member';
    renderBody(message({ conversation_key: PUBLIC_CHANNEL_KEY }));
    expect(await screen.findByTestId('share-payload-report')).toBeTruthy();
  });

  it('a report quota error names the report limit, not the share quota', async () => {
    await setup('always');
    const inner = fetchSpy.getMockImplementation() as (
      input: RequestInfo | URL,
      init?: RequestInit
    ) => Promise<Response>;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/report')
        ? jsonResponse({ detail: { code: 'share_quota_key', params: {} } }, { status: 429 })
        : inner(input, init)
    );
    renderBody(message({ conversation_key: PUBLIC_CHANNEL_KEY }));
    fireEvent.click(await screen.findByTestId('share-payload-report'));
    fireEvent.click(await screen.findByTestId('share-report-submit'));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(toastMock.error).toHaveBeenCalledWith(i18n.t('sharePayload.report.quota'));
    expect(i18n.t('sharePayload.report.quota')).not.toBe(i18n.t('errors.share_quota_key'));
  });
});
