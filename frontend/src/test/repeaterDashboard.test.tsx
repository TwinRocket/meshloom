import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { RepeaterDashboard } from '../components/RepeaterDashboard';
import i18n from '../i18n';
import bEn from '../i18n/locales/slices/b.en.json';
import bFr from '../i18n/locales/slices/b.fr.json';
import fEn from '../i18n/locales/slices/f.en.json';
import fFr from '../i18n/locales/slices/f.fr.json';

i18n.addResourceBundle('en', 'translation', bEn, true, true);
i18n.addResourceBundle('fr', 'translation', bFr, true, true);
i18n.addResourceBundle('en', 'translation', fEn, true, true);
i18n.addResourceBundle('fr', 'translation', fFr, true, true);
import type { UseRepeaterDashboardResult } from '../hooks/useRepeaterDashboard';
import type { Contact, Conversation } from '../types';

// Mock the hook — typed as mutable version of the return type
const mockHook: {
  -readonly [K in keyof UseRepeaterDashboardResult]: UseRepeaterDashboardResult[K];
} = {
  loggedIn: false,
  loginLoading: false,
  loginError: null,
  lastLoginAttempt: null,
  paneData: {
    status: null,
    nodeInfo: null,
    neighbors: null,
    acl: null,
    radioSettings: null,
    advertIntervals: null,
    ownerInfo: null,
    lppTelemetry: null,
    regions: null,
  },
  paneStates: {
    status: { loading: false, attempt: 0, error: null },
    nodeInfo: { loading: false, attempt: 0, error: null },
    neighbors: { loading: false, attempt: 0, error: null },
    acl: { loading: false, attempt: 0, error: null },
    radioSettings: { loading: false, attempt: 0, error: null },
    advertIntervals: { loading: false, attempt: 0, error: null },
    ownerInfo: { loading: false, attempt: 0, error: null },
    lppTelemetry: { loading: false, attempt: 0, error: null },
    regions: { loading: false, attempt: 0, error: null },
  },
  consoleHistory: [],
  consoleLoading: false,
  login: vi.fn(),
  loginAsGuest: vi.fn(),
  resetLogin: vi.fn(),
  refreshPane: vi.fn(),
  loadAll: vi.fn(),
  cancelLoadAll: vi.fn(),
  queuedPanes: [],
  loadAllProgress: null,
  sendConsoleCommand: vi.fn(),
  sendZeroHopAdvert: vi.fn(),
  sendFloodAdvert: vi.fn(),
  rebootRepeater: vi.fn(),
  syncClock: vi.fn(),
};

vi.mock('../hooks/useRepeaterDashboard', () => ({
  useRepeaterDashboard: () => mockHook,
}));

// Mock api module (TelemetryHistoryPane fetches on mount)
vi.mock('../api', () => ({
  api: {
    repeaterTelemetryHistory: vi.fn().mockResolvedValue([]),
    setContactRoutingOverride: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

// Mock sonner toast
vi.mock('../components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

// Mock leaflet imports (not needed in test)
vi.mock('react-leaflet', () => ({
  MapContainer: () => null,
  TileLayer: () => null,
  CircleMarker: () => null,
  Popup: () => null,
  Polyline: () => null,
}));

const REPEATER_KEY = 'aa'.repeat(32);

const conversation: Conversation = {
  type: 'contact',
  id: REPEATER_KEY,
  name: 'TestRepeater',
};

const contacts: Contact[] = [
  {
    public_key: REPEATER_KEY,
    name: 'TestRepeater',
    type: 2,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  },
];

const defaultProps = {
  conversation,
  contacts,
  radioLat: null,
  radioLon: null,
  radioName: null,
  onTrace: vi.fn(),
  onPathDiscovery: vi.fn(async () => {
    throw new Error('unused');
  }),
  onToggleFavorite: vi.fn(),
  onDeleteContact: vi.fn(),
  trackedTelemetryRepeaters: [] as string[],
  onToggleTrackedTelemetry: vi.fn(async () => {}),
};

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('RepeaterDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset mock hook state
    mockHook.loggedIn = false;
    mockHook.loginLoading = false;
    mockHook.loginError = null;
    mockHook.lastLoginAttempt = null;
    localStorage.clear();
    mockHook.paneData = {
      status: null,
      nodeInfo: null,
      neighbors: null,
      acl: null,
      radioSettings: null,
      advertIntervals: null,
      ownerInfo: null,
      lppTelemetry: null,
      regions: null,
    };
    mockHook.paneStates = {
      status: { loading: false, attempt: 0, error: null },
      nodeInfo: { loading: false, attempt: 0, error: null },
      neighbors: { loading: false, attempt: 0, error: null },
      acl: { loading: false, attempt: 0, error: null },
      radioSettings: { loading: false, attempt: 0, error: null },
      advertIntervals: { loading: false, attempt: 0, error: null },
      ownerInfo: { loading: false, attempt: 0, error: null },
      lppTelemetry: { loading: false, attempt: 0, error: null },
      regions: { loading: false, attempt: 0, error: null },
    };
    mockHook.consoleHistory = [];
    mockHook.consoleLoading = false;
  });

  it('renders login form when not logged in', () => {
    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('repeater.loginPassword'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.loginGuest'))).toBeInTheDocument();
    expect(screen.getByPlaceholderText(i18n.t('repeater.passwordPlaceholder'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.loginDescription'))).toBeInTheDocument();
  });

  it('renders dashboard panes when logged in', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('repeater.telemetry'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.nodeInfo'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.neighbors'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.acl'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.radioSettings'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.advertIntervals'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.lppSensors'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.ownerInfo'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.actions'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.console'))).toBeInTheDocument();
  });

  it('shows not fetched placeholder for empty panes', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    // All panes should show <not fetched> since data is null
    const notFetched = screen.getAllByText(i18n.t('repeater.notFetched'));
    expect(notFetched.length).toBeGreaterThanOrEqual(7); // At least 7 data panes (incl. LPP Sensors)
  });

  it('shows Load All button when logged in', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('repeater.loadAll'))).toBeInTheDocument();
  });

  it('calls loadAll when Load All button is clicked', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    fireEvent.click(screen.getByText(i18n.t('repeater.loadAll')));
    expect(mockHook.loadAll).toHaveBeenCalledTimes(1);
  });

  it('shows login error when present', () => {
    mockHook.loginError = i18n.t('toast.loginFailed');

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('toast.loginFailed'))).toBeInTheDocument();
  });

  it('returns to an empty login form when re-entering the password', () => {
    const storageKey = `meshloom-server-password:repeater:${REPEATER_KEY}`;
    localStorage.setItem(storageKey, JSON.stringify({ password: 'wrong-password' }));
    mockHook.loggedIn = true;
    mockHook.lastLoginAttempt = {
      method: 'password',
      outcome: 'not_confirmed',
      summary: i18n.t('repeater.loginNotConfirmedSummary'),
      details: null,
      heardBack: false,
      at: Date.now(),
    };
    mockHook.resetLogin = vi.fn(() => {
      mockHook.loggedIn = false;
      mockHook.lastLoginAttempt = null;
    });

    const { rerender } = render(<RepeaterDashboard {...defaultProps} />);

    fireEvent.click(screen.getByText(i18n.t('repeater.reenterPassword')));
    expect(mockHook.resetLogin).toHaveBeenCalledTimes(1);

    rerender(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByPlaceholderText(i18n.t('repeater.passwordPlaceholder'))).toHaveValue('');
    expect(localStorage.getItem(storageKey)).toBe(null);
  });

  it('shows pane error when fetch fails', () => {
    mockHook.loggedIn = true;
    mockHook.paneStates.status = {
      loading: false,
      attempt: 3,
      error: i18n.t('toast.requestFailed'),
    };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('toast.requestFailed'))).toBeInTheDocument();
  });

  it('shows GPS unavailable message for neighbors when repeater coords are missing', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.neighbors = {
      neighbors: [
        { pubkey_prefix: 'bbbbbbbbbbbb', name: 'Neighbor', snr: 7.2, last_heard_seconds: 9 },
      ],
    };
    mockHook.paneData.nodeInfo = {
      name: 'TestRepeater',
      lat: '0',
      lon: '0',
      clock_utc: null,
    };
    mockHook.paneStates.neighbors = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };
    mockHook.paneStates.nodeInfo = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('repeater.mapUnavailable'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.posMissing'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('repeater.dist'))).not.toBeInTheDocument();
  });

  it('shows neighbor distance when repeater node info includes valid coords', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.neighbors = {
      neighbors: [
        { pubkey_prefix: 'bbbbbbbbbbbb', name: 'Neighbor', snr: 7.2, last_heard_seconds: 9 },
      ],
    };
    mockHook.paneData.nodeInfo = {
      name: 'TestRepeater',
      lat: '-31.9500',
      lon: '115.8600',
      clock_utc: null,
    };
    mockHook.paneStates.neighbors = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };
    mockHook.paneStates.nodeInfo = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };

    const contactsWithNeighbor = [
      ...contacts,
      {
        public_key: 'bbbbbbbbbbbb0000000000000000000000000000000000000000000000000000',
        name: 'Neighbor',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: 0,
        direct_path_hash_mode: 0,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: -31.94,
        lon: 115.87,
        last_seen: null,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      },
    ];

    render(<RepeaterDashboard {...defaultProps} contacts={contactsWithNeighbor} />);

    expect(screen.getByText(i18n.t('repeater.dist'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.posReported'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('repeater.mapUnavailable'))).not.toBeInTheDocument();
  });

  it('uses advert coords for neighbor distance when node info is unavailable', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.neighbors = {
      neighbors: [
        { pubkey_prefix: 'bbbbbbbbbbbb', name: 'Neighbor', snr: 7.2, last_heard_seconds: 9 },
      ],
    };
    mockHook.paneData.nodeInfo = null;
    mockHook.paneStates.neighbors = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };
    mockHook.paneStates.nodeInfo = {
      loading: false,
      attempt: 0,
      error: null,
      fetched_at: null,
    };

    const contactsWithAdvertAndNeighbor = [
      {
        ...contacts[0],
        lat: -31.95,
        lon: 115.86,
      },
      {
        public_key: 'bbbbbbbbbbbb0000000000000000000000000000000000000000000000000000',
        name: 'Neighbor',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: 0,
        direct_path_hash_mode: 0,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: -31.94,
        lon: 115.87,
        last_seen: null,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      },
    ];

    render(<RepeaterDashboard {...defaultProps} contacts={contactsWithAdvertAndNeighbor} />);

    expect(screen.getByText(i18n.t('repeater.dist'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.posAdvert'))).toBeInTheDocument();
  });

  it('sorts the neighbors table when column headers are clicked', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.neighbors = {
      neighbors: [
        { pubkey_prefix: 'cccccccccccc', name: 'Mike', snr: 5.0, last_heard_seconds: 20 },
        { pubkey_prefix: 'dddddddddddd', name: 'Zeta', snr: 9.0, last_heard_seconds: 30 },
        { pubkey_prefix: 'eeeeeeeeeeee', name: 'Alpha', snr: 1.0, last_heard_seconds: 10 },
      ],
    };
    mockHook.paneStates.neighbors = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };

    render(<RepeaterDashboard {...defaultProps} />);

    // Scope to the Neighbors table; the dashboard renders multiple panes at once.
    const table = screen
      .getByRole('columnheader', { name: new RegExp(i18n.t('repeater.lastHeard'), 'i') })
      .closest('table')!;
    // The first child of each name cell is the bare name text node (the prefix
    // lives in a nested span), so this reads exactly the neighbor name.
    const names = () =>
      within(table)
        .getAllByRole('row')
        .slice(1)
        .map((r) => r.querySelector('td')?.firstChild?.textContent ?? '');
    const header = (re: RegExp) => within(table).getByRole('columnheader', { name: re });

    // Default order is SNR descending (preserves the pre-sorting behavior).
    expect(names()).toEqual(['Zeta', 'Mike', 'Alpha']);

    // Name ascending, then toggle to descending on a second click.
    fireEvent.click(header(new RegExp(i18n.t('repeater.name'), 'i')));
    expect(names()).toEqual(['Alpha', 'Mike', 'Zeta']);
    fireEvent.click(header(new RegExp(i18n.t('repeater.name'), 'i')));
    expect(names()).toEqual(['Zeta', 'Mike', 'Alpha']);

    // Last Heard ascending surfaces the most recently heard neighbor first.
    fireEvent.click(header(new RegExp(i18n.t('repeater.lastHeard'), 'i')));
    expect(names()).toEqual(['Alpha', 'Mike', 'Zeta']);
  });

  it('shows fetching state with attempt counter', () => {
    mockHook.loggedIn = true;
    mockHook.paneStates.status = { loading: true, attempt: 2, error: null };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(
      screen.getByText(i18n.t('repeater.fetchingAttempt', { attempt: 2, max: 3 }))
    ).toBeInTheDocument();
  });

  it('renders telemetry data when available', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.status = {
      battery_volts: 4.2,
      tx_queue_len: 0,
      noise_floor_dbm: -120,
      last_rssi_dbm: -85,
      last_snr_db: 7.5,
      packets_received: 100,
      packets_sent: 50,
      airtime_seconds: 600,
      rx_airtime_seconds: 1200,
      uptime_seconds: 86400,
      sent_flood: 10,
      sent_direct: 40,
      recv_flood: 30,
      recv_direct: 70,
      flood_dups: 1,
      direct_dups: 0,
      full_events: 0,
      recv_errors: 5,
      telemetry_history: [],
    };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('4.200V')).toBeInTheDocument();
    expect(screen.getByText('-120 dBm')).toBeInTheDocument();
    expect(screen.getByText('7.5 dB')).toBeInTheDocument();
  });

  it('formats the radio tuple and preserves the raw tuple in a tooltip', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.radioSettings = {
      firmware_version: 'v1.0',
      radio: '910.5250244,62.5,7,5',
      tx_power: '20',
      airtime_factor: '0',
      duty_cycle_limit: '100.0%',
      repeat_enabled: '1',
      flood_max: '3',
    };

    render(<RepeaterDashboard {...defaultProps} />);

    const formatted = screen.getByText('910.525 MHz, BW 62.5 kHz, SF7, CR5');
    expect(formatted).toBeInTheDocument();
    expect(formatted).toHaveAttribute('title', '910.5250244,62.5,7,5');
  });

  it('shows fetched time and relative age when pane data has been loaded', () => {
    mockHook.loggedIn = true;
    mockHook.paneStates.status = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(new RegExp(i18n.t('repeater.justNow')))).toBeInTheDocument();
  });

  it('keeps repeater clock drift anchored to fetch time across remounts', () => {
    vi.useFakeTimers();
    try {
      const fetchedAt = Date.UTC(2024, 0, 1, 12, 0, 0);
      vi.setSystemTime(fetchedAt);

      mockHook.loggedIn = true;
      mockHook.paneData.nodeInfo = {
        name: 'TestRepeater',
        lat: null,
        lon: null,
        clock_utc: '11:59:30 - 1/1/2024 UTC',
      };
      mockHook.paneStates.nodeInfo = {
        loading: false,
        attempt: 1,
        error: null,
        fetched_at: fetchedAt,
      };

      const firstRender = render(<RepeaterDashboard {...defaultProps} />);
      expect(screen.getByText(i18n.t('repeater.clockDrift', { text: '30s' }))).toBeInTheDocument();

      vi.setSystemTime(fetchedAt + 10 * 60 * 1000);
      firstRender.unmount();

      render(<RepeaterDashboard {...defaultProps} />);
      expect(screen.getByText(i18n.t('repeater.clockDrift', { text: '30s' }))).toBeInTheDocument();
      expect(
        screen.queryByText(i18n.t('repeater.clockDrift', { text: '10m30s' }))
      ).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('renders action buttons', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('repeater.zeroHopAdvert'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.sendFloodAdvert'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.syncClock'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('repeater.reboot'))).toBeInTheDocument();
  });

  it('calls onTrace when trace button clicked', () => {
    render(<RepeaterDashboard {...defaultProps} />);

    // The trace button has title "Direct Trace"
    fireEvent.click(screen.getByTitle(i18n.t('repeater.directTrace')));
    expect(defaultProps.onTrace).toHaveBeenCalledTimes(1);
  });

  it('keeps the repeater key out of the header', () => {
    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.queryByText(REPEATER_KEY)).not.toBeInTheDocument();
    expect(screen.queryByText(REPEATER_KEY.slice(0, 12))).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t('chatHeader.showKey'))).not.toBeInTheDocument();
  });

  it('uses larger header action hit targets', () => {
    render(<RepeaterDashboard {...defaultProps} />);

    const deleteBtn = screen.getByRole('button', { name: i18n.t('repeater.delete') });
    expect(deleteBtn.className).toContain('p-2');
    expect(deleteBtn.className).toContain('h-9');
    expect(deleteBtn.className).toContain('ml-1.5');
  });

  it('console shows placeholder when empty', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(i18n.t('repeater.consoleEmpty'))).toBeInTheDocument();
  });

  it('console input does not autocapitalise or autocorrect', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    const input = screen.getByLabelText(i18n.t('repeater.consoleAria'));
    expect(input).toHaveAttribute('autocapitalize', 'none');
    expect(input).toHaveAttribute('autocorrect', 'off');
    expect(input).toHaveAttribute('spellcheck', 'false');
  });

  describe('console command history recall', () => {
    const sent = (command: string) => ({
      command,
      response: '',
      timestamp: 0,
      outgoing: true,
    });

    beforeEach(() => {
      mockHook.loggedIn = true;
      mockHook.consoleHistory = [sent('advert'), sent('advert'), sent('clock sync')];
    });

    it('arrow up recalls the last command', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      const input = screen.getByLabelText(i18n.t('repeater.consoleAria')) as HTMLInputElement;
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      expect(input.value).toBe('clock sync');
    });

    it('repeated arrow up walks further back, deduping consecutive repeats', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      const input = screen.getByLabelText(i18n.t('repeater.consoleAria')) as HTMLInputElement;
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      expect(input.value).toBe('advert');

      // Only two distinct entries, so the third press is a no-op
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      expect(input.value).toBe('advert');
    });

    it('arrow down returns toward the empty input', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      const input = screen.getByLabelText(i18n.t('repeater.consoleAria')) as HTMLInputElement;
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      fireEvent.keyDown(input, { key: 'ArrowDown' });
      expect(input.value).toBe('clock sync');

      fireEvent.keyDown(input, { key: 'ArrowDown' });
      expect(input.value).toBe('');
    });

    it('is a no-op with empty history', () => {
      mockHook.consoleHistory = [];

      render(<RepeaterDashboard {...defaultProps} />);

      const input = screen.getByLabelText(i18n.t('repeater.consoleAria')) as HTMLInputElement;
      fireEvent.change(input, { target: { value: 'partial' } });
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      expect(input.value).toBe('partial');
    });

    it('links out to the MeshCore CLI docs', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      const link = screen.getByRole('link', { name: i18n.t('repeater.cliDocs') });
      expect(link).toHaveAttribute('href', 'https://docs.meshcore.io/cli_commands/');
      expect(link).toHaveAttribute('target', '_blank');
    });

    it('help palette stays closed until opened', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      expect(
        screen.queryByRole('button', { name: i18n.t('repeater.insertCommand', { command: 'ver' }) })
      ).not.toBeInTheDocument();
    });

    it('help palette inserts a frequent command into the input', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      fireEvent.click(screen.getByRole('button', { name: i18n.t('repeater.help') }));
      fireEvent.click(
        screen.getByRole('button', { name: i18n.t('repeater.insertCommand', { command: 'ver' }) })
      );

      const input = screen.getByLabelText(i18n.t('repeater.consoleAria')) as HTMLInputElement;
      expect(input.value).toBe('ver');
    });
  });

  describe('path type display and reset', () => {
    it('shows flood when direct_path_len is -1', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getByText(i18n.t('contactInfo.flood'))).toBeInTheDocument();
    });

    it('shows direct when direct_path_len is 0', () => {
      const directContacts: Contact[] = [
        { ...contacts[0], direct_path_len: 0, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      expect(screen.getByText(i18n.t('contactInfo.hopDirect'))).toBeInTheDocument();
    });

    it('shows N hops when direct_path_len > 0', () => {
      const hoppedContacts: Contact[] = [
        { ...contacts[0], direct_path_len: 3, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={hoppedContacts} />);

      expect(screen.getByText(i18n.t('contactInfo.hopCount', { count: 3 }))).toBeInTheDocument();
    });

    it('shows 1 hop (singular) for single hop', () => {
      const oneHopContacts: Contact[] = [
        { ...contacts[0], direct_path_len: 1, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={oneHopContacts} />);

      expect(screen.getByText(i18n.t('contactInfo.hopCount', { count: 1 }))).toBeInTheDocument();
    });

    it('direct path is clickable, underlined, and marked as editable', () => {
      const directContacts: Contact[] = [
        { ...contacts[0], direct_path_len: 0, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      const directEl = screen.getByTitle(i18n.t('contactInfo.editRouting'));
      expect(directEl).toBeInTheDocument();
      expect(directEl.textContent).toBe(i18n.t('contactInfo.hopDirect'));
      expect(directEl.className).toContain('underline');
    });

    it('shows forced decorator when a routing override is active', () => {
      const forcedContacts: Contact[] = [
        {
          ...contacts[0],
          direct_path_len: 1,
          last_seen: 1700000000,
          route_override_path: 'ae92f13e',
          route_override_len: 2,
          route_override_hash_mode: 1,
        },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={forcedContacts} />);

      expect(screen.getByText(i18n.t('contactInfo.hopCount', { count: 2 }))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.forced'))).toBeInTheDocument();
    });

    it('clicking direct path opens modal and can force direct routing', async () => {
      const directContacts: Contact[] = [
        { ...contacts[0], direct_path_len: 0, last_seen: 1700000000 },
      ];

      const { api } = await import('../api');
      const overrideSpy = vi.spyOn(api, 'setContactRoutingOverride').mockResolvedValue({
        status: 'ok',
        public_key: REPEATER_KEY,
      });

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      fireEvent.click(screen.getByTitle(i18n.t('contactInfo.editRouting')));
      expect(await screen.findByRole('dialog')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: i18n.t('contactInfo.forceDirect') }));

      await waitFor(() => {
        expect(overrideSpy).toHaveBeenCalledWith(REPEATER_KEY, '0');
      });

      overrideSpy.mockRestore();
    });

    it('closing the routing override modal does not call the API', async () => {
      const directContacts: Contact[] = [
        { ...contacts[0], direct_path_len: 0, last_seen: 1700000000 },
      ];

      const { api } = await import('../api');
      const overrideSpy = vi.spyOn(api, 'setContactRoutingOverride').mockResolvedValue({
        status: 'ok',
        public_key: REPEATER_KEY,
      });

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      fireEvent.click(screen.getByTitle(i18n.t('contactInfo.editRouting')));
      expect(await screen.findByRole('dialog')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: i18n.t('contactInfo.cancel') }));

      expect(overrideSpy).not.toHaveBeenCalled();

      overrideSpy.mockRestore();
    });
  });

  describe('telemetry history', () => {
    beforeEach(async () => {
      const { api } = await import('../api');
      vi.mocked(api.repeaterTelemetryHistory).mockResolvedValue([]);
    });

    it('loads telemetry history on mount when logged in', async () => {
      const { api } = await import('../api');
      mockHook.loggedIn = true;

      render(<RepeaterDashboard {...defaultProps} />);

      await waitFor(() => {
        expect(api.repeaterTelemetryHistory).toHaveBeenCalledWith(REPEATER_KEY);
      });
    });

    it('shows telemetry history pane in logged-in view even before status fetch', () => {
      mockHook.loggedIn = true;

      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getByText(i18n.t('repeater.telemetryHistory'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('repeater.noHistory'))).toBeInTheDocument();
    });

    it('updates history from live status fetch', async () => {
      const { api } = await import('../api');
      const historySpy = vi.mocked(api.repeaterTelemetryHistory);
      const liveEntry = { timestamp: 1700000000, data: { battery_volts: 4.2 } };
      historySpy.mockResolvedValue([]);

      mockHook.loggedIn = true;
      mockHook.paneData.status = {
        battery_volts: 4.2,
        tx_queue_len: 0,
        noise_floor_dbm: -120,
        last_rssi_dbm: -85,
        last_snr_db: 7.5,
        packets_received: 100,
        packets_sent: 50,
        airtime_seconds: 600,
        rx_airtime_seconds: 1200,
        uptime_seconds: 86400,
        sent_flood: 10,
        sent_direct: 40,
        recv_flood: 30,
        recv_direct: 70,
        flood_dups: 1,
        direct_dups: 0,
        full_events: 0,
        recv_errors: null,
        telemetry_history: [liveEntry],
      };

      render(<RepeaterDashboard {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(i18n.t('repeater.samples', { count: 1 }))).toBeInTheDocument();
      });
    });

    it('does not let an older preload overwrite newer live status history', async () => {
      const { api } = await import('../api');
      const historySpy = vi.mocked(api.repeaterTelemetryHistory);
      const deferred = createDeferred<{ timestamp: number; data: { battery_volts: number } }[]>();
      historySpy.mockReturnValue(deferred.promise);

      mockHook.loggedIn = true;
      mockHook.paneData.status = {
        battery_volts: 4.2,
        tx_queue_len: 0,
        noise_floor_dbm: -120,
        last_rssi_dbm: -85,
        last_snr_db: 7.5,
        packets_received: 100,
        packets_sent: 50,
        airtime_seconds: 600,
        rx_airtime_seconds: 1200,
        uptime_seconds: 86400,
        sent_flood: 10,
        sent_direct: 40,
        recv_flood: 30,
        recv_direct: 70,
        flood_dups: 1,
        direct_dups: 0,
        full_events: 0,
        recv_errors: null,
        telemetry_history: [{ timestamp: 1700000000, data: { battery_volts: 4.2 } }],
      };

      render(<RepeaterDashboard {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(i18n.t('repeater.samples', { count: 1 }))).toBeInTheDocument();
      });

      deferred.resolve([{ timestamp: 1690000000, data: { battery_volts: 3.9 } }]);
      await deferred.promise;

      expect(screen.getByText(i18n.t('repeater.samples', { count: 1 }))).toBeInTheDocument();
    });
  });

  describe('load all queue feedback', () => {
    beforeEach(() => {
      mockHook.loggedIn = true;
    });

    it('marks a queued pane that already holds values without hiding them', () => {
      mockHook.paneData.nodeInfo = {
        name: 'QueuedNodeName',
        lat: null,
        lon: null,
        clock_utc: null,
      };
      mockHook.paneStates.nodeInfo = {
        loading: false,
        attempt: 1,
        error: null,
        fetched_at: Date.now(),
        queued: true,
      };

      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getByText('QueuedNodeName')).toBeInTheDocument();
      expect(screen.getByText(i18n.t('repeater.queuedBadge'))).toBeInTheDocument();
    });

    it('marks a queued pane the same way whether or not it holds values', () => {
      mockHook.paneStates.nodeInfo = {
        loading: false,
        attempt: 0,
        error: null,
        fetched_at: null,
        queued: true,
      };
      mockHook.paneStates.regions = {
        loading: false,
        attempt: 1,
        error: null,
        fetched_at: Date.now(),
        queued: true,
      };

      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getAllByText(i18n.t('repeater.queuedBadge'))).toHaveLength(2);
    });

    it('keeps the values on screen while the pane is being refreshed', () => {
      mockHook.paneData.nodeInfo = {
        name: 'QueuedNodeName',
        lat: null,
        lon: null,
        clock_utc: null,
      };
      mockHook.paneStates.nodeInfo = {
        loading: true,
        attempt: 1,
        error: null,
        fetched_at: Date.now(),
        queued: false,
      };

      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getByText('QueuedNodeName')).toBeInTheDocument();
      expect(screen.getByText(i18n.t('repeater.fetching'))).toBeInTheDocument();
    });

    it('reports the retry attempt on a pane that still shows its values', () => {
      mockHook.paneData.nodeInfo = {
        name: 'QueuedNodeName',
        lat: null,
        lon: null,
        clock_utc: null,
      };
      mockHook.paneStates.nodeInfo = {
        loading: true,
        attempt: 2,
        error: null,
        fetched_at: Date.now(),
        queued: false,
      };

      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getByText('QueuedNodeName')).toBeInTheDocument();
      expect(
        screen.getByText(i18n.t('repeater.fetchingAttempt', { attempt: 2, max: 3 }))
      ).toBeInTheDocument();
    });

    it('a pane being fetched reports fetching, not queueing', () => {
      mockHook.paneStates.nodeInfo = {
        loading: true,
        attempt: 1,
        error: null,
        fetched_at: null,
        queued: true,
      };

      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getByText(i18n.t('repeater.fetching'))).toBeInTheDocument();
      expect(screen.queryByText(i18n.t('repeater.queuedBadge'))).not.toBeInTheDocument();
    });

    it('replaces Load All with progress and a way out while a run is going', () => {
      mockHook.loadAllProgress = { done: 3, total: 9 };

      render(<RepeaterDashboard {...defaultProps} />);

      expect(
        screen.getByText(i18n.t('repeater.loadAllProgress', { done: 3, total: 9 }))
      ).toBeInTheDocument();
      expect(screen.queryByText(i18n.t('repeater.loadAll'))).not.toBeInTheDocument();

      fireEvent.click(screen.getByText(i18n.t('repeater.stopLoading')));
      expect(mockHook.cancelLoadAll).toHaveBeenCalled();

      mockHook.loadAllProgress = null;
    });
  });
});
