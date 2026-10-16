import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageList } from '../components/MessageList';
import i18n from '../i18n';
import sliceBEn from '../i18n/locales/slices/b.en.json';
import sliceBFr from '../i18n/locales/slices/b.fr.json';
import sliceEEn from '../i18n/locales/slices/e.en.json';
import sliceEFr from '../i18n/locales/slices/e.fr.json';
import { recordRawPacket, resetRawPacketStore } from '../stores/rawPacketStore';
import type { Message } from '../types';
import {
  RESEND_WINDOW_SECONDS,
  noteLiveOutgoingChannelMessage,
  resendAge,
  resetLiveOutgoingSightings,
  sendAnchorMs,
} from '../utils/resendWindow';

i18n.addResourceBundle('en', 'translation', sliceBEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceBFr, true, true);
i18n.addResourceBundle('en', 'translation', sliceEEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceEFr, true, true);

vi.mock('../api', () => ({
  api: {
    getPacketObserverReachCounts: vi.fn(async () => ({ directory_enabled: false, counts: {} })),
    resolveDirectoryHops: vi.fn(async () => ({ resolved: {} })),
  },
  formatApiError: (err: unknown) => String(err),
}));

const NOW_MS = 1_800_000_000_000;
const NOW_SEC = NOW_MS / 1000;

function outgoingChan(senderTimestamp: number, id = 7): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: 'C3B889530D4F02DB5662EA13C417F530',
    text: 'Me: hello',
    sender_timestamp: senderTimestamp,
    received_at: senderTimestamp,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: true,
    acked: 0,
    sender_name: 'Me',
  };
}

/** Buffer live packets stamped by a server whose clock is `skewSec` behind the browser. */
function seedServerClockBehindBy(skewSec: number) {
  for (let i = 0; i < 5; i += 1) {
    recordRawPacket({
      id: i + 1,
      observation_id: i + 1,
      timestamp: Math.floor(NOW_SEC) - skewSec,
      received_at_ms: NOW_MS,
      data: `aa${i}`,
      decrypted: false,
      payload_type: 'Unknown',
      rssi: -60,
      snr: 5,
      decrypted_info: null,
    });
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW_MS);
});

afterEach(() => {
  vi.useRealTimers();
  resetRawPacketStore();
  resetLiveOutgoingSightings();
});

describe('resendAge (browser clock)', () => {
  it('uses the server timestamp as is, but not as authoritative, when no skew is measured', () => {
    expect(resendAge(outgoingChan(NOW_SEC - 10), NOW_MS, null)).toEqual({
      ageSec: 10,
      authoritative: false,
    });
  });

  it('treats a measured zero skew as authoritative', () => {
    expect(resendAge(outgoingChan(NOW_SEC - 10), NOW_MS, 0)).toEqual({
      ageSec: 10,
      authoritative: true,
    });
  });

  it('corrects a server clock 2 min behind with the measured offset', () => {
    // Sent 5 s ago on the browser clock, stamped 125 s ago by the server.
    expect(resendAge(outgoingChan(NOW_SEC - 125), NOW_MS, 120)).toEqual({
      ageSec: 5,
      authoritative: true,
    });
  });

  it('corrects a server clock 10 min ahead with the measured offset', () => {
    expect(resendAge(outgoingChan(NOW_SEC + 595), NOW_MS, -600)?.ageSec).toBe(5);
  });

  it('gives no age for a future stamp when the skew is unknown (server ahead)', () => {
    // Server 4 min ahead, page reloaded: no sighting, no live packet buffered.
    expect(resendAge(outgoingChan(NOW_SEC + 240), NOW_MS, null)).toBeNull();
  });

  it('absorbs a second of jitter without a measured skew', () => {
    expect(resendAge(outgoingChan(NOW_SEC + 1), NOW_MS, null)).toEqual({
      ageSec: 0,
      authoritative: false,
    });
  });

  it('prefers the browser time the message was seen live over any skew', () => {
    const msg = outgoingChan(NOW_SEC + 3600);
    noteLiveOutgoingChannelMessage(msg, NOW_MS - 12_000);
    expect(resendAge(msg, NOW_MS, null)).toEqual({ ageSec: 12, authoritative: true });
    // A later sighting (WS echo after the send response) keeps the earliest one.
    noteLiveOutgoingChannelMessage(msg, NOW_MS - 2_000);
    expect(resendAge(msg, NOW_MS, 0)?.ageSec).toBe(12);
  });

  it('ignores incoming and direct messages', () => {
    noteLiveOutgoingChannelMessage({ id: 1, type: 'CHAN', outgoing: false }, NOW_MS);
    noteLiveOutgoingChannelMessage({ id: 2, type: 'PRIV', outgoing: true }, NOW_MS);
    expect(resendAge({ id: 1, sender_timestamp: NOW_SEC - 100 }, NOW_MS, 0)?.ageSec).toBe(100);
    expect(resendAge({ id: 2, sender_timestamp: NOW_SEC - 100 }, NOW_MS, 0)?.ageSec).toBe(100);
  });

  it('returns null without any anchor', () => {
    expect(resendAge({ id: 3, sender_timestamp: null }, NOW_MS, 0)).toBeNull();
  });
});

describe('sendAnchorMs', () => {
  const start = NOW_MS - 25_000;
  const response = NOW_MS;

  it('uses the response time without a measured skew', () => {
    expect(sendAnchorMs(NOW_SEC - 600, start, response, null)).toBe(response);
  });

  it('uses the stamp converted to browser time after a radio lock wait', () => {
    // Stamped 20 s after the request left (radio busy), server 2 min behind.
    const stampedBrowserSec = (start + 20_000) / 1000;
    expect(sendAnchorMs(stampedBrowserSec - 120, start, response, 120)).toBe(start + 20_000);
  });

  it('bounds the converted stamp by the request start and the response', () => {
    expect(sendAnchorMs(NOW_SEC - 3600, start, response, 0)).toBe(start);
    expect(sendAnchorMs(NOW_SEC + 3600, start, response, 0)).toBe(response);
  });
});

describe('MessageList resend window with a skewed server clock', () => {
  async function openStatusModal() {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByLabelText(i18n.t('messageList.noEchoes')));
    return user;
  }

  it('offers resend for a fresh message when the server is 2 min behind, and sends the browser age', async () => {
    seedServerClockBehindBy(120);
    const onResend = vi.fn();
    render(
      <MessageList
        messages={[outgoingChan(NOW_SEC - 120 - 3)]}
        contacts={[]}
        loading={false}
        onResendChannelMessage={onResend}
      />
    );

    const user = await openStatusModal();
    await user.click(await screen.findByText(i18n.t('path.resend')));

    expect(onResend).toHaveBeenCalledTimes(1);
    const [id, newTimestamp, ageSec] = onResend.mock.calls[0];
    expect(id).toBe(7);
    expect(newTimestamp).toBe(false);
    expect(ageSec).toBeGreaterThanOrEqual(3);
    expect(ageSec).toBeLessThan(RESEND_WINDOW_SECONDS);
  });

  it('hides resend once 30 s have passed on the browser clock when the server is ahead', async () => {
    const msg = outgoingChan(NOW_SEC + 600 - 40);
    // Seen live 40 s ago: the server stamp (10 min ahead) looks like the future.
    noteLiveOutgoingChannelMessage(msg, NOW_MS - 40_000);
    render(
      <MessageList
        messages={[msg]}
        contacts={[]}
        loading={false}
        onResendChannelMessage={vi.fn()}
      />
    );

    await openStatusModal();
    expect(await screen.findByText(i18n.t('path.resendNew'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('path.resend'))).not.toBeInTheDocument();
  });

  it('expires the button on the browser clock', async () => {
    const msg = outgoingChan(NOW_SEC + 600);
    noteLiveOutgoingChannelMessage(msg, NOW_MS - 25_000);
    render(
      <MessageList
        messages={[msg]}
        contacts={[]}
        loading={false}
        onResendChannelMessage={vi.fn()}
      />
    );

    const user = await openStatusModal();
    expect(await screen.findByText(i18n.t('path.resend'))).toBeInTheDocument();
    await user.keyboard('{Escape}');

    vi.advanceTimersByTime(6_000);
    await openStatusModal();
    expect(await screen.findByText(i18n.t('path.resendNew'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('path.resend'))).not.toBeInTheDocument();
  });

  it('hides resend after a reload when the server is ahead and no skew is measured', async () => {
    // Server 4 min ahead, message sent 1 min ago: no sighting, no live packet.
    render(
      <MessageList
        messages={[outgoingChan(NOW_SEC + 240 - 60)]}
        contacts={[]}
        loading={false}
        onResendChannelMessage={vi.fn()}
      />
    );

    await openStatusModal();
    expect(await screen.findByText(i18n.t('path.resendNew'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('path.resend'))).not.toBeInTheDocument();
  });

  it('sends no client age when the skew is unknown, so the server clock decides', async () => {
    const onResend = vi.fn();
    render(
      <MessageList
        messages={[outgoingChan(NOW_SEC - 5)]}
        contacts={[]}
        loading={false}
        onResendChannelMessage={onResend}
      />
    );

    const user = await openStatusModal();
    await user.click(await screen.findByText(i18n.t('path.resend')));

    expect(onResend).toHaveBeenCalledWith(7, false, undefined);
  });
});
