import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    resolveDirectoryHops: vi.fn().mockResolvedValue({ resolved: {} }),
  },
}));

import { api } from '../api';

import './eSlices';
import { PathModal } from '../components/PathModal';
import i18n from '../i18n';
import { CONTACT_TYPE_REPEATER, type Contact, type RadioConfig } from '../types';
import type { SenderInfo } from '../utils/pathUtils';

function createContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: 'AAAAAAAAAAAABBBBBBBBBBBBCCCCCCCCCCCCDDDDDDDDDDDDEEEEEEEEEEEE',
    name: 'Test Contact',
    type: CONTACT_TYPE_REPEATER,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: -1,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
    ...overrides,
  };
}

const senderInfo: SenderInfo = {
  name: 'Sender',
  publicKeyOrPrefix: '5E' + 'E'.repeat(62),
  lat: null,
  lon: null,
};

const config: RadioConfig = {
  public_key: 'FF' + 'F'.repeat(62),
  name: 'MyRadio',
  lat: 0,
  lon: 0,
  tx_power: 10,
  max_tx_power: 20,
  radio: { freq: 915, bw: 250, sf: 10, cr: 8 },
  path_hash_mode: 0,
  path_hash_mode_supported: false,
};

function renderPath(path: string, pathLen: number, contacts: Contact[], directoryEnabled = true) {
  return render(
    <PathModal
      open
      onClose={() => {}}
      paths={[{ path, path_len: pathLen, received_at: 1700000000 }]}
      senderInfo={senderInfo}
      contacts={contacts}
      config={config}
      directoryEnabled={directoryEnabled}
    />
  );
}

describe('PathModal local hop names', () => {
  beforeEach(() => {
    vi.mocked(api.resolveDirectoryHops).mockReset();
    vi.mocked(api.resolveDirectoryHops).mockResolvedValue({ resolved: {} });
  });

  it('keeps 1-byte hops as hex only (no name, no green icon)', () => {
    const repeater = createContact({
      public_key: '1A' + 'A'.repeat(62),
      name: 'HillTop',
      type: CONTACT_TYPE_REPEATER,
    });

    renderPath('1A', 1, [repeater]);

    expect(screen.getAllByText('1A').length).toBeGreaterThan(0);
    expect(screen.queryByText('HillTop')).not.toBeInTheDocument();
    expect(screen.queryByTestId('local-repeater-icon')).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t('path.unknown'))).not.toBeInTheDocument();
  });

  it('shows name and green icon for a unique 2-byte type-2 match', () => {
    const repeater = createContact({
      public_key: '1A2B' + 'A'.repeat(60),
      name: 'HillTop',
      type: CONTACT_TYPE_REPEATER,
    });

    renderPath('1A2B', 1, [repeater]);

    expect(screen.getByText('HillTop')).toBeInTheDocument();
    expect(screen.getByTestId('local-repeater-icon')).toBeInTheDocument();
  });

  it('shows name and green icon for a unique 3-byte type-2 match', () => {
    const repeater = createContact({
      public_key: '1A2B3C' + 'A'.repeat(58),
      name: 'Ridge',
      type: CONTACT_TYPE_REPEATER,
    });

    renderPath('1A2B3C', 1, [repeater]);

    expect(screen.getByText('Ridge')).toBeInTheDocument();
    expect(screen.getByTestId('local-repeater-icon')).toBeInTheDocument();
  });

  it('shows UNKNOWN for a 2-byte hop with no local match', () => {
    renderPath('FFFF', 1, []);

    expect(screen.getAllByText('FFFF').length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('path.unknown'))).toBeInTheDocument();
    expect(screen.queryByTestId('local-repeater-icon')).not.toBeInTheDocument();
  });

  it('shows the existing ambiguous warning without guessing a name', () => {
    const a = createContact({
      public_key: '1A2B' + 'A'.repeat(60),
      name: 'HillTop',
      type: CONTACT_TYPE_REPEATER,
    });
    const b = createContact({
      public_key: '1A2B' + 'B'.repeat(60),
      name: 'Valley',
      type: CONTACT_TYPE_REPEATER,
    });

    renderPath('1A2B', 1, [a, b]);

    expect(screen.getByText(i18n.t('path.ambiguous'))).toBeInTheDocument();
    expect(screen.getByText('HillTop')).toBeInTheDocument();
    expect(screen.getByText('Valley')).toBeInTheDocument();
    expect(screen.queryByTestId('local-repeater-icon')).not.toBeInTheDocument();
    expect(screen.queryByTestId('directory-globe-icon')).not.toBeInTheDocument();
  });

  it('shows an orange globe for a CoreScope name on an unknown 2-byte hop', async () => {
    vi.mocked(api.resolveDirectoryHops).mockResolvedValue({
      resolved: {
        FFFF: { name: 'RemoteHill', source: 'corescope', hash_width: 2 },
      },
    });

    renderPath('FFFF', 1, []);

    await waitFor(() => {
      expect(screen.getByText('RemoteHill')).toBeInTheDocument();
    });
    expect(screen.getByTestId('directory-globe-icon')).toBeInTheDocument();
    expect(screen.queryByTestId('local-repeater-icon')).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t('path.unknown'))).not.toBeInTheDocument();
  });

  it('asks the Community directory nothing while Community is off', async () => {
    vi.mocked(api.resolveDirectoryHops).mockResolvedValue({
      resolved: {
        FFFF: { name: 'RemoteHill', source: 'corescope', hash_width: 2 },
      },
    });

    renderPath('FFFF', 1, [], false);

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.resolveDirectoryHops).not.toHaveBeenCalled();
    expect(screen.queryByText('RemoteHill')).not.toBeInTheDocument();
    expect(screen.queryByTestId('directory-globe-icon')).not.toBeInTheDocument();
  });

  it('does not request or show CoreScope for a local unique type-2', async () => {
    const repeater = createContact({
      public_key: '1A2B' + 'A'.repeat(60),
      name: 'HillTop',
      type: CONTACT_TYPE_REPEATER,
    });

    renderPath('1A2B', 1, [repeater]);

    expect(screen.getByText('HillTop')).toBeInTheDocument();
    expect(screen.getByTestId('local-repeater-icon')).toBeInTheDocument();
    expect(screen.queryByTestId('directory-globe-icon')).not.toBeInTheDocument();
    expect(api.resolveDirectoryHops).not.toHaveBeenCalled();
  });

  it('does not request CoreScope for 1-byte hops', () => {
    renderPath('1A', 1, []);

    expect(api.resolveDirectoryHops).not.toHaveBeenCalled();
    expect(screen.queryByTestId('directory-globe-icon')).not.toBeInTheDocument();
  });
});
