import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ContactInfoPane } from '../components/ContactInfoPane';
import i18n from '../i18n';
import sliceEn from '../i18n/locales/slices/b.en.json';
import sliceFr from '../i18n/locales/slices/b.fr.json';

i18n.addResourceBundle('en', 'translation', sliceEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceFr, true, true);
import type { Contact, ContactAnalytics } from '../types';

const { getContactAnalytics, contactTelemetryHistory } = vi.hoisted(() => ({
  getContactAnalytics: vi.fn(),
  contactTelemetryHistory: vi.fn(),
}));

vi.mock('../api', () => ({
  api: {
    getContactAnalytics,
    contactTelemetryHistory,
  },
  isAbortError: () => false,
}));

vi.mock('../components/ui/sheet', () => ({
  Sheet: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../components/ContactAvatar', () => ({
  ContactAvatar: () => <div data-testid="contact-avatar" />,
}));

vi.mock('react-leaflet', () => ({
  MapContainer: () => null,
  TileLayer: () => null,
  CircleMarker: () => null,
  Popup: () => null,
}));

vi.mock('../components/ui/sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

function createContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: 'AA'.repeat(32),
    name: 'Alice',
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: 1700000000,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: 1699990000,
    ...overrides,
  };
}

function createAnalytics(
  contact: Contact | null,
  overrides: Partial<ContactAnalytics> = {}
): ContactAnalytics {
  return {
    lookup_type: contact ? 'contact' : 'name',
    name: contact?.name ?? 'Mystery',
    contact,
    name_first_seen_at: null,
    name_history: [],
    dm_message_count: 0,
    channel_message_count: 0,
    includes_direct_messages: Boolean(contact),
    most_active_rooms: [],
    advert_paths: [],
    advert_frequency: null,
    nearest_repeaters: [],
    hourly_activity: Array.from({ length: 24 }, (_, index) => ({
      bucket_start: 1_700_000_000 + index * 3600,
      last_24h_count: 0,
      last_week_average: 0,
      all_time_average: 0,
    })),
    weekly_activity: Array.from({ length: 26 }, (_, index) => ({
      bucket_start: 1_700_000_000 + index * 604800,
      message_count: 0,
    })),
    ...overrides,
  };
}

const baseProps = {
  fromChannel: false,
  onClose: () => {},
  contacts: [] as Contact[],
  config: null,
  onToggleFavorite: () => {},
  onSearchMessagesByKey: vi.fn(),
  onSearchMessagesByName: vi.fn(),
};

describe('ContactInfoPane', () => {
  beforeEach(() => {
    getContactAnalytics.mockReset();
    contactTelemetryHistory.mockReset();
    contactTelemetryHistory.mockResolvedValue([]);
    baseProps.onSearchMessagesByKey = vi.fn();
    baseProps.onSearchMessagesByName = vi.fn();
  });

  it('shows hop width when contact has a stored path hash mode', async () => {
    const contact = createContact({ direct_path_hash_mode: 1, direct_path_len: 1 });
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    await screen.findByText(contact.public_key);
    await waitFor(() => {
      expect(screen.getByText(i18n.t('contactInfo.hopWidth'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.byteIds', { n: 2 }))).toBeInTheDocument();
    });
  });

  it('drops cached analytics when the pane closes, so reopening shows loading', async () => {
    const contact = createContact();
    getContactAnalytics.mockResolvedValueOnce(createAnalytics(contact));

    const { rerender } = render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);
    await screen.findByText(contact.public_key);

    rerender(<ContactInfoPane {...baseProps} contactKey={null} />);
    getContactAnalytics.mockReturnValueOnce(new Promise(() => {}));
    rerender(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    expect(screen.getByText(i18n.t('contactInfo.loading'))).toBeInTheDocument();
    expect(screen.queryByText(contact.public_key)).not.toBeInTheDocument();
    expect(getContactAnalytics).toHaveBeenCalledTimes(2);
  });

  it('keeps the loaded analytics and does not refetch when the language changes', async () => {
    const contact = createContact();
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));
    const initialLanguage = i18n.language;

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);
    await screen.findByText(contact.public_key);

    try {
      await act(async () => {
        await i18n.changeLanguage(initialLanguage === 'fr' ? 'en' : 'fr');
      });
      expect(getContactAnalytics).toHaveBeenCalledTimes(1);
      expect(screen.getByText(contact.public_key)).toBeInTheDocument();
      expect(screen.queryByText(i18n.t('contactInfo.loading'))).not.toBeInTheDocument();
    } finally {
      await act(async () => {
        await i18n.changeLanguage(initialLanguage);
      });
    }
  });

  it('does not show hop width for flood-routed contacts', async () => {
    const contact = createContact({ direct_path_len: -1, direct_path_hash_mode: -1 });
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    await screen.findByText('Alice');
    await waitFor(() => {
      expect(screen.queryByText(i18n.t('contactInfo.hopWidth'))).not.toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.flood'))).toBeInTheDocument();
    });
  });

  it('shows forced routing override and learned route separately', async () => {
    const contact = createContact({
      direct_path_len: 1,
      direct_path_hash_mode: 0,
      route_override_path: 'ae92f13e',
      route_override_len: 2,
      route_override_hash_mode: 1,
    });
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    await screen.findByText('Alice');
    await waitFor(() => {
      expect(screen.getByText(i18n.t('contactInfo.routing'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.forced'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.learnedRoute'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.hopCount', { count: 1 }))).toBeInTheDocument();
    });
  });

  it('loads name-only channel stats and most active channels', async () => {
    getContactAnalytics.mockResolvedValue(
      createAnalytics(null, {
        lookup_type: 'name',
        name: 'Mystery',
        name_first_seen_at: 1_699_999_000,
        channel_message_count: 4,
        most_active_rooms: [
          {
            channel_key: 'ab'.repeat(16),
            channel_name: '#ops',
            message_count: 3,
          },
        ],
        hourly_activity: Array.from({ length: 24 }, (_, index) => ({
          bucket_start: 1_700_000_000 + index * 3600,
          last_24h_count: index === 23 ? 2 : 0,
          last_week_average: index === 23 ? 1.5 : 0,
          all_time_average: index === 23 ? 1.2 : 0,
        })),
        weekly_activity: Array.from({ length: 26 }, (_, index) => ({
          bucket_start: 1_700_000_000 + index * 604800,
          message_count: index === 25 ? 4 : 0,
        })),
      })
    );

    render(<ContactInfoPane {...baseProps} contactKey="name:Mystery" fromChannel />);

    await screen.findByText('Mystery');
    await waitFor(() => {
      expect(getContactAnalytics).toHaveBeenCalledWith(
        { name: 'Mystery' },
        expect.any(AbortSignal)
      );
      expect(screen.getByText(i18n.t('contactInfo.messages'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.channelMessages'))).toBeInTheDocument();
      expect(screen.getByText('4', { selector: 'p' })).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.nameFirstInUse'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.perHour'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.perWeek'))).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.mostActive'))).toBeInTheDocument();
      expect(screen.getByText('#ops')).toBeInTheDocument();
      expect(
        screen.getByText(i18n.t('contactInfo.nameOnlyHelp'), { exact: false })
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          i18n.t('contactInfo.attribution', { kind: i18n.t('contactInfo.attributionName') }),
          { exact: false }
        )
      ).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.searchByName'))).toBeInTheDocument();
    });
  });

  it('fires the name search callback from the name-only pane', async () => {
    getContactAnalytics.mockResolvedValue(
      createAnalytics(null, { lookup_type: 'name', name: 'Mystery' })
    );

    render(<ContactInfoPane {...baseProps} contactKey="name:Mystery" fromChannel />);

    const button = await screen.findByRole('button', { name: i18n.t('contactInfo.searchByName') });
    button.click();

    expect(baseProps.onSearchMessagesByName).toHaveBeenCalledWith('Mystery');
  });

  it('shows alias note in the channel attribution warning for keyed contacts', async () => {
    const contact = createContact();
    getContactAnalytics.mockResolvedValue(
      createAnalytics(contact, {
        name_history: [
          { name: 'Alice', first_seen: 1000, last_seen: 2000 },
          { name: 'AliceOld', first_seen: 900, last_seen: 999 },
        ],
      })
    );

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} fromChannel />);

    await screen.findByText(contact.public_key);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: i18n.t('contactInfo.aka') })).toBeInTheDocument();
      const aliasNote = i18n.t('contactInfo.attributionAlias').trim();
      expect(screen.getByText(aliasNote, { exact: false })).toBeInTheDocument();
      expect(screen.getByText(i18n.t('contactInfo.searchByKey'))).toBeInTheDocument();
    });
  });

  it('shows the RF locate CTA even without a GPS section', async () => {
    const contact = createContact({ lat: null, lon: null });
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));
    const onLocateContact = vi.fn();

    render(
      <ContactInfoPane
        {...baseProps}
        contactKey={contact.public_key}
        onLocateContact={onLocateContact}
      />
    );

    const button = await screen.findByTestId('locate-contact-cta');
    expect(screen.queryByText(i18n.t('contactInfo.location'))).not.toBeInTheDocument();
    button.click();
    expect(onLocateContact).toHaveBeenCalledWith(contact.public_key);
  });

  it('fires the key search callback from the keyed pane', async () => {
    const contact = createContact();
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    const button = await screen.findByRole('button', { name: i18n.t('contactInfo.searchByKey') });
    button.click();

    expect(baseProps.onSearchMessagesByKey).toHaveBeenCalledWith(contact.public_key);
  });

  it('copies the official contact share URI and can show a QR', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const contact = createContact();
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('share.copyUri') }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(
        `meshcore://contact/add?name=Alice&public_key=${'aa'.repeat(32)}&type=1`
      );
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('share.showQr') }));
    expect(screen.getByTitle(i18n.t('share.qrTitle'))).toBeInTheDocument();
  });

  it('does not offer a share URI for name-only or unofficial contacts', async () => {
    getContactAnalytics.mockResolvedValue(
      createAnalytics(null, { lookup_type: 'name', name: 'Mystery' })
    );
    const { rerender } = render(
      <ContactInfoPane {...baseProps} contactKey="name:Mystery" fromChannel />
    );
    await screen.findByText('Mystery');
    expect(screen.queryByRole('button', { name: i18n.t('share.copyUri') })).not.toBeInTheDocument();

    const prefixContact = createContact({ public_key: 'aabbccddeeff', type: 1 });
    getContactAnalytics.mockResolvedValue(createAnalytics(prefixContact));
    rerender(<ContactInfoPane {...baseProps} contactKey={prefixContact.public_key} />);
    await screen.findByText('Alice');
    expect(screen.queryByRole('button', { name: i18n.t('share.copyUri') })).not.toBeInTheDocument();
  });
});
