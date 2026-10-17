import { Profiler } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import './eSlices';
import { CrackerPanel } from '../components/CrackerPanel';
import i18n from '../i18n';
import { recordRawPacket, resetRawPacketStore } from '../stores/rawPacketStore';
import type { RawPacket } from '../types';

const crackerMock = vi.hoisted(() => ({
  crack: vi.fn(),
}));

vi.mock('meshcore-hashtag-cracker', async (importOriginal) => {
  const actual = await importOriginal<typeof import('meshcore-hashtag-cracker')>();
  return {
    ...actual,
    isWebGpuSupported: () => true,
    GroupTextCracker: class {
      isGpuAvailable() {
        return true;
      }
      destroy() {}
      setWordlist() {}
      abort() {}
      crack(...args: unknown[]) {
        return crackerMock.crack(...args);
      }
    },
  };
});

vi.mock('nosleep.js', () => ({
  default: class {
    enable() {}
    disable() {}
  },
}));

vi.mock('../api', () => ({
  api: {
    getUndecryptedPacketCount: vi.fn(),
    getGroupTextSamples: vi.fn(),
  },
}));

vi.mock('../components/ui/sonner', () => ({
  toast: {
    error: vi.fn(),
  },
}));

import { api } from '../api';

const mockedApi = vi.mocked(api);
const GROUP_TEXT_PACKET =
  '1500d9b5d4330c3bfc80e2114278944c79dad5760f3b1baa407d7786765eabdf97f90d9c9d';

function createGroupTextPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 99,
    observation_id: 99,
    timestamp: 1700000000,
    data: GROUP_TEXT_PACKET,
    payload_type: 'GROUP_TEXT',
    snr: 7.5,
    rssi: -80,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

describe('CrackerPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRawPacketStore();
    mockedApi.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mockedApi.getGroupTextSamples.mockResolvedValue({
      hash_count: 0,
      packet_count: 0,
      scanned: 0,
      samples: [],
    });
    crackerMock.crack.mockReset();
    crackerMock.crack.mockResolvedValue({ found: false });
  });

  it('allows clearing max length while editing', async () => {
    render(<CrackerPanel channels={[]} onChannelCreate={vi.fn()} visible={false} />);

    await waitFor(() => {
      expect(mockedApi.getUndecryptedPacketCount).toHaveBeenCalled();
    });

    const maxLengthInput = screen.getByLabelText(i18n.t('cracker.maxLength')) as HTMLInputElement;
    fireEvent.change(maxLengthInput, { target: { value: '' } });

    expect(maxLengthInput.value).toBe('');
  });

  it('does not re-render while collapsed when packets arrive', async () => {
    let commits = 0;
    render(
      <Profiler
        id="cracker-collapsed"
        onRender={() => {
          commits += 1;
        }}
      >
        <CrackerPanel channels={[]} onChannelCreate={vi.fn()} visible={false} />
      </Profiler>
    );

    await waitFor(() => {
      expect(mockedApi.getUndecryptedPacketCount).toHaveBeenCalled();
    });

    const before = commits;
    act(() => {
      recordRawPacket(createGroupTextPacket());
    });
    expect(commits).toBe(before);
    expect(screen.getByText(i18n.t('cracker.pending')).closest('span')).toHaveTextContent('0');
  });

  it('reads the current packet buffer when shown again', async () => {
    const { rerender } = render(
      <CrackerPanel channels={[]} onChannelCreate={vi.fn()} visible={false} />
    );

    await waitFor(() => {
      expect(mockedApi.getUndecryptedPacketCount).toHaveBeenCalled();
    });

    act(() => {
      recordRawPacket(createGroupTextPacket());
    });
    expect(screen.getByText(i18n.t('cracker.pending')).closest('span')).toHaveTextContent('0');

    rerender(<CrackerPanel channels={[]} onChannelCreate={vi.fn()} visible />);

    await waitFor(() => {
      expect(screen.getByText(i18n.t('cracker.pending')).closest('span')).toHaveTextContent('1');
    });
  });

  it('hydrates historical samples when started with no live packets', async () => {
    mockedApi.getGroupTextSamples.mockResolvedValue({
      hash_count: 1,
      packet_count: 3,
      scanned: 12,
      samples: [
        {
          channel_hash: 'd9',
          packet_id: 41,
          data: GROUP_TEXT_PACKET,
          timestamp: 1_700_000_000,
          cipher_mac: 'b5d4',
        },
      ],
    });
    crackerMock.crack.mockResolvedValue({
      found: true,
      roomName: 'test',
      key: '9cd8fcf22a47333b591d96a2b848b73f',
      decryptedMessage: 'Alice: Hello',
    });
    const onChannelCreate = vi.fn().mockResolvedValue(undefined);
    render(<CrackerPanel channels={[]} onChannelCreate={onChannelCreate} visible />);

    const start = await screen.findByRole('button', { name: i18n.t('cracker.findChannels') });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);

    await waitFor(() => {
      expect(mockedApi.getGroupTextSamples).toHaveBeenCalledWith(30);
      expect(crackerMock.crack).toHaveBeenCalled();
      expect(onChannelCreate).toHaveBeenCalledWith(
        '#test',
        '9cd8fcf22a47333b591d96a2b848b73f',
        true
      );
    });
  });

  it('shows the listening state while running with an empty queue', async () => {
    render(<CrackerPanel channels={[]} onChannelCreate={vi.fn()} visible />);

    const start = await screen.findByRole('button', { name: i18n.t('cracker.findChannels') });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);

    expect(await screen.findByText(i18n.t('cracker.waitingTraffic'))).toBeInTheDocument();
    expect(crackerMock.crack).not.toHaveBeenCalled();
  });

  it('widens only hydrated sample searches when older history is included', async () => {
    mockedApi.getGroupTextSamples.mockResolvedValue({
      hash_count: 1,
      packet_count: 1,
      scanned: 1,
      samples: [
        {
          channel_hash: 'd9',
          packet_id: 43,
          data: GROUP_TEXT_PACKET,
          timestamp: 1_700_000_000,
          cipher_mac: 'b5d4',
        },
      ],
    });
    render(<CrackerPanel channels={[]} onChannelCreate={vi.fn()} visible />);

    fireEvent.click(await screen.findByLabelText(i18n.t('cracker.includeOlder')));
    const start = screen.getByRole('button', { name: i18n.t('cracker.findChannels') });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);

    await waitFor(() => {
      expect(mockedApi.getGroupTextSamples).toHaveBeenCalledWith(3650);
      expect(crackerMock.crack).toHaveBeenCalledWith(
        GROUP_TEXT_PACKET,
        expect.objectContaining({ validSeconds: 3650 * 86400 }),
        expect.any(Function)
      );
    });
  });

  it('passes the historical checkbox choice when creating a found channel', async () => {
    mockedApi.getGroupTextSamples.mockResolvedValue({
      hash_count: 1,
      packet_count: 1,
      scanned: 1,
      samples: [
        {
          channel_hash: 'd9',
          packet_id: 42,
          data: GROUP_TEXT_PACKET,
          timestamp: 1_700_000_000,
          cipher_mac: 'b5d4',
        },
      ],
    });
    crackerMock.crack.mockResolvedValue({
      found: true,
      roomName: 'test',
      key: '9cd8fcf22a47333b591d96a2b848b73f',
      decryptedMessage: 'Alice: Hello',
    });
    const onChannelCreate = vi.fn().mockResolvedValue(undefined);
    render(<CrackerPanel channels={[]} onChannelCreate={onChannelCreate} visible />);

    fireEvent.click(screen.getByLabelText(i18n.t('cracker.decryptHistorical')));
    act(() => {
      recordRawPacket(createGroupTextPacket());
    });
    const start = await screen.findByRole('button', { name: i18n.t('cracker.findChannels') });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);

    await waitFor(() => {
      expect(mockedApi.getGroupTextSamples).not.toHaveBeenCalled();
      expect(onChannelCreate).toHaveBeenCalledWith(
        '#test',
        '9cd8fcf22a47333b591d96a2b848b73f',
        false
      );
    });
  });

  it('keeps other same-hash samples after one name is found', async () => {
    const collisionPacket =
      '1500d9ffff330c3bfc80e2114278944c79dad5760f3b1baa407d7786765eabdf97f90d9c9d';
    mockedApi.getGroupTextSamples.mockResolvedValue({
      hash_count: 1,
      packet_count: 2,
      scanned: 2,
      samples: [
        {
          channel_hash: 'd9',
          packet_id: 41,
          data: GROUP_TEXT_PACKET,
          timestamp: 1_700_000_000,
          cipher_mac: 'b5d4',
        },
        {
          channel_hash: 'd9',
          packet_id: 42,
          data: collisionPacket,
          timestamp: 1_700_000_001,
          cipher_mac: 'ffff',
        },
      ],
    });
    crackerMock.crack
      .mockResolvedValueOnce({
        found: true,
        roomName: 'test',
        key: '9cd8fcf22a47333b591d96a2b848b73f',
        decryptedMessage: 'Alice: Hello',
      })
      .mockResolvedValue({ found: false });
    const onChannelCreate = vi.fn().mockResolvedValue(undefined);
    render(<CrackerPanel channels={[]} onChannelCreate={onChannelCreate} visible />);

    const start = await screen.findByRole('button', { name: i18n.t('cracker.findChannels') });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);

    await waitFor(() => {
      expect(onChannelCreate).toHaveBeenCalledTimes(1);
      expect(screen.getByText(i18n.t('cracker.found')).closest('span')).toHaveTextContent('1');
      expect(screen.getByText(i18n.t('cracker.failed')).closest('span')).toHaveTextContent('1');
    });
  });
});
