import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import './eSlices';
import { VisualizerView } from '../components/VisualizerView';
import i18n from '../i18n';
import { recordRawPacket, resetRawPacketStore, seedRawPacketStore } from '../stores/rawPacketStore';
import type { RawPacket } from '../types';
import {
  resetVisualizerFocusHandoff,
  setVisualizerFocusHandoff,
  VISUALIZER_FOCUS_FILTER_IDS_KEY,
  VISUALIZER_FOCUS_OBSERVATION_KEY,
  VISUALIZER_FOCUS_PACKET_HASH_KEY,
} from '../utils/visualizerFocusHandoff';

// The 3D scene needs WebGL, which jsdom does not provide.
vi.mock('../components/PacketVisualizer3D', () => ({
  PacketVisualizer3D: ({ focusHandoff }: { focusHandoff?: { observationKey?: string } | null }) => (
    <div
      data-testid="packet-visualizer-3d"
      data-focus-observation={focusHandoff?.observationKey ?? ''}
    />
  ),
}));

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

describe('VisualizerView packet feed', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetVisualizerFocusHandoff();
    resetRawPacketStore();
  });

  it('opens the packet analyzer when a feed packet is clicked', () => {
    seedRawPacketStore({ packets: [createPacket({ id: 7, observation_id: 21 })] });
    render(<VisualizerView contacts={[]} channels={[]} config={null} />);

    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();

    // Desktop split-pane and mobile tab both render the feed, so take the first.
    fireEvent.click(screen.getAllByRole('button', { name: /TF/ })[0]);

    expect(screen.getByText(i18n.t('rawPacket.details'))).toBeInTheDocument();
  });

  it('does not render the analyzer until a packet is selected', () => {
    render(<VisualizerView contacts={[]} channels={[]} config={null} />);

    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();
  });
});

describe('VisualizerView focus handoff', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetVisualizerFocusHandoff();
    resetRawPacketStore();
  });

  it('consumes the handoff and highlights a live observation', () => {
    setVisualizerFocusHandoff({
      observationKey: 'obs-21',
      packetHash: 'aabbccddeeff0011',
      filterIds: ['TEXT'],
    });
    seedRawPacketStore({
      packets: [createPacket({ id: 7, observation_id: 21, packet_hash: 'aabbccddeeff0011' })],
    });

    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).toBe('obs-21');
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_PACKET_HASH_KEY)).toBe('aabbccddeeff0011');
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_FILTER_IDS_KEY)).toBe('["TEXT"]');
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).not.toContain('000000000000');

    render(<VisualizerView contacts={[]} channels={[]} config={null} />);

    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).toBeNull();
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_PACKET_HASH_KEY)).toBeNull();
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_FILTER_IDS_KEY)).toBeNull();
    expect(screen.getByText(i18n.t('rawPacket.details'))).toBeInTheDocument();
    expect(screen.getByTestId('packet-visualizer-3d')).toHaveAttribute(
      'data-focus-observation',
      'obs-21'
    );
  });

  it('does not reopen the inspector after it is closed when live packets keep arriving', () => {
    setVisualizerFocusHandoff({ observationKey: 'obs-21' });
    seedRawPacketStore({ packets: [createPacket({ id: 7, observation_id: 21 })] });

    render(<VisualizerView contacts={[]} channels={[]} config={null} />);
    expect(screen.getByText(i18n.t('rawPacket.details'))).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();

    recordRawPacket(createPacket({ id: 8, observation_id: 22 }));
    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();
    expect(screen.getByTestId('packet-visualizer-3d')).toHaveAttribute(
      'data-focus-observation',
      'obs-21'
    );
  });

  it('consumes a missing observation after reconnect without crashing', () => {
    setVisualizerFocusHandoff({ observationKey: 'obs-404', packetHash: 'deadbeefdeadbeef' });

    expect(() => {
      render(<VisualizerView contacts={[]} channels={[]} config={null} />);
    }).not.toThrow();

    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).toBeNull();
    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();
    expect(screen.getByTestId('packet-visualizer-3d')).toBeInTheDocument();
  });

  it('does not highlight when the live store has a different observation', () => {
    setVisualizerFocusHandoff({ observationKey: 'obs-99' });
    seedRawPacketStore({ packets: [createPacket({ id: 7, observation_id: 21 })] });

    render(<VisualizerView contacts={[]} channels={[]} config={null} />);

    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).toBeNull();
    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();
  });
});
