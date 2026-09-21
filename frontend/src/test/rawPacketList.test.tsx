import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import './eSlices';
import i18n from '../i18n';
import { RawPacketList } from '../components/RawPacketList';
import type { Channel, RawPacket } from '../types';

const CHANNEL_KEY = 'aabbccddeeff00112233445566778899';
const GROUP_DATA_PACKET = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';

const GD_CHANNEL: Channel = {
  key: CHANNEL_KEY,
  name: '#gd',
  is_hashtag: true,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

function createPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 1,
    timestamp: 1700000000,
    data: '000000000000',
    payload_type: 'REQ',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

function createGroupDataPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return createPacket({
    data: GROUP_DATA_PACKET,
    payload_type: 'GroupData',
    ...overrides,
  });
}

describe('RawPacketList', () => {
  it('renders TF badge for transport-flood packets', () => {
    render(<RawPacketList packets={[createPacket()]} />);

    expect(screen.getByText('TF')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('makes packet cards clickable only when an inspector handler is provided', () => {
    const packet = createPacket({ id: 9, observation_id: 22 });
    const onPacketClick = vi.fn();

    render(<RawPacketList packets={[packet]} onPacketClick={onPacketClick} />);

    fireEvent.click(screen.getByRole('button'));

    expect(onPacketClick).toHaveBeenCalledWith(packet);
  });

  it('sticks to the bottom on new packets when autoScroll is on, and holds when off', () => {
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get: () => 500,
    });
    try {
      const { container, rerender } = render(
        <RawPacketList packets={[createPacket({ id: 1 })]} autoScroll />
      );
      const list = container.querySelector('.overflow-y-auto') as HTMLElement;

      rerender(
        <RawPacketList packets={[createPacket({ id: 1 }), createPacket({ id: 2 })]} autoScroll />
      );
      expect(list.scrollTop).toBe(500);

      // Pause autoscroll, simulate the user scrolling up, then receive a packet.
      list.scrollTop = 0;
      rerender(
        <RawPacketList
          packets={[createPacket({ id: 1 }), createPacket({ id: 2 }), createPacket({ id: 3 })]}
          autoScroll={false}
        />
      );
      expect(list.scrollTop).toBe(0);
    } finally {
      delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight;
    }
  });
  it('does not promise incoming packets while the radio is down', () => {
    const { rerender } = render(<RawPacketList packets={[]} />);
    expect(screen.getByText(i18n.t('rawPacket.empty'))).toBeInTheDocument();

    rerender(<RawPacketList packets={[]} radioOffline />);
    expect(screen.queryByText(i18n.t('rawPacket.empty'))).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.emptyRadioOffline'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.emptyRadioOfflineHint'))).toBeInTheDocument();
  });

  it('does not mutate packet.decrypted when client-decoding GroupData', () => {
    const packet = createGroupDataPacket();
    render(<RawPacketList packets={[packet]} channels={[GD_CHANNEL]} />);

    expect(packet.decrypted).toBe(false);
    expect(screen.getByText(i18n.t('rawPacket.decryptedOpen'))).toBeInTheDocument();
  });

  it('opens the lock and uses channel/type when group_data is present', () => {
    const packet = createGroupDataPacket({
      decrypted_info: {
        channel_name: '#sensors',
        sender: null,
        channel_key: CHANNEL_KEY,
        contact_key: null,
        sender_timestamp: null,
        message: null,
        group_data: {
          data_type: 0x00ab,
          data_len: 9,
          data_hex: '73656e736f722d6f6b',
          data_text: 'sensor-ok',
        },
      },
    });

    render(<RawPacketList packets={[packet]} />);

    expect(packet.decrypted).toBe(false);
    expect(screen.getByText(i18n.t('rawPacket.decryptedOpen'))).toBeInTheDocument();
    expect(screen.getByText(/#sensors/)).toBeInTheDocument();
    expect(screen.getByText(/0x00ab/i)).toBeInTheDocument();
  });

  it('opens the lock by client-parsing GroupData with a matching local channel key', () => {
    const packet = createGroupDataPacket();
    render(<RawPacketList packets={[packet]} channels={[GD_CHANNEL]} />);

    expect(packet.decrypted).toBe(false);
    expect(screen.getByText(i18n.t('rawPacket.decryptedOpen'))).toBeInTheDocument();
    expect(screen.getByText(/0x00ab/i)).toBeInTheDocument();
  });

  it('hides the lock on cleartext types and shows a muted lock when still encrypted', () => {
    render(
      <RawPacketList
        packets={[
          createPacket({
            id: 1,
            observation_id: 1,
            data: '1100aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899',
            payload_type: 'Advert',
          }),
          createGroupDataPacket({ id: 2, observation_id: 2 }),
        ]}
      />
    );

    expect(screen.queryByText(i18n.t('rawPacket.decryptedOpen'))).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.encrypted'))).toBeInTheDocument();
  });

  it('shows a ×N badge for distinct observations that share packet_hash', () => {
    const onRepeatFilter = vi.fn();
    const onPacketClick = vi.fn();
    render(
      <RawPacketList
        packets={[
          createPacket({ id: 1, observation_id: 1, packet_hash: 'deadbeef', data: 'aa' }),
          createPacket({ id: 1, observation_id: 2, packet_hash: 'deadbeef', data: 'bb' }),
        ]}
        onPacketClick={onPacketClick}
        onRepeatFilter={onRepeatFilter}
      />
    );

    const badges = screen.getAllByRole('button', {
      name: i18n.t('rawPacket.repeatFilterAria', { count: 2 }),
    });
    expect(badges).toHaveLength(2);
    expect(badges[0].closest('[role="button"]')).toBeNull();
    fireEvent.click(badges[0]);
    expect(onRepeatFilter).toHaveBeenCalledWith('deadbeef');
    expect(onPacketClick).not.toHaveBeenCalled();
  });
});
