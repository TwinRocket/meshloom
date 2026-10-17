import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChannelInfoPane } from '../components/ChannelInfoPane';
import i18n from '../i18n';
import sliceEn from '../i18n/locales/slices/b.en.json';
import sliceFr from '../i18n/locales/slices/b.fr.json';

i18n.addResourceBundle('en', 'translation', sliceEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceFr, true, true);
import type { Channel, ChannelDetail } from '../types';

// Mock the api module
vi.mock('../api', () => ({
  api: {
    getChannelDetail: vi.fn(),
  },
}));

import { api } from '../api';
const mockGetChannelDetail = vi.mocked(api.getChannelDetail);

function makeChannel(key: string, name: string, isHashtag: boolean): Channel {
  return {
    key,
    name,
    is_hashtag: isHashtag,
    on_radio: false,
    last_read_at: null,
    favorite: false,
    muted: false,
  };
}

function makeDetail(channel: Channel): ChannelDetail {
  return {
    channel,
    message_counts: { last_1h: 0, last_24h: 0, last_48h: 0, last_7d: 0, all_time: 0 },
    first_message_at: null,
    unique_sender_count: 0,
    top_senders_24h: [],
    path_hash_width_24h: {
      total_packets: 0,
      single_byte: 0,
      double_byte: 0,
      triple_byte: 0,
      single_byte_pct: 0,
      double_byte_pct: 0,
      triple_byte_pct: 0,
    },
  };
}

const noop = () => {};

const baseProps = {
  onClose: noop,
  onToggleFavorite: noop,
};

describe('ChannelInfoPane key visibility', () => {
  it('shows key directly for hashtag channels', async () => {
    const key = 'AA'.repeat(16);
    const channel = makeChannel(key, '#general', true);
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);

    await waitFor(() => {
      expect(screen.getByText(key.toLowerCase())).toBeInTheDocument();
    });
    expect(screen.queryByText(i18n.t('channelInfo.showKey'))).not.toBeInTheDocument();
  });

  it('hides key behind "Show Key" button for private channels', async () => {
    const key = 'BB'.repeat(16);
    const channel = makeChannel(key, 'Secret', false);
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);

    await waitFor(() => {
      expect(screen.getByText('Secret')).toBeInTheDocument();
    });
    expect(screen.queryByText(key.toLowerCase())).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('channelInfo.showKey'))).toBeInTheDocument();
  });

  it('reveals key when "Show Key" is clicked', async () => {
    const key = 'CC'.repeat(16);
    const channel = makeChannel(key, 'Private', false);
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);

    await waitFor(() => {
      expect(screen.getByText(i18n.t('channelInfo.showKey'))).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(i18n.t('channelInfo.showKey')));

    expect(screen.getByText(key.toLowerCase())).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('channelInfo.showKey'))).not.toBeInTheDocument();
  });

  it('resets key visibility when channel changes', async () => {
    const key1 = 'DD'.repeat(16);
    const key2 = 'EE'.repeat(16);
    const ch1 = makeChannel(key1, 'Room1', false);
    const ch2 = makeChannel(key2, 'Room2', false);
    mockGetChannelDetail.mockImplementation((key) =>
      Promise.resolve(key === key1 ? makeDetail(ch1) : makeDetail(ch2))
    );

    const { rerender } = render(
      <ChannelInfoPane {...baseProps} channelKey={key1} channels={[ch1, ch2]} />
    );

    await waitFor(() => {
      expect(screen.getByText(i18n.t('channelInfo.showKey'))).toBeInTheDocument();
    });

    // Reveal key for first channel
    fireEvent.click(screen.getByText(i18n.t('channelInfo.showKey')));
    expect(screen.getByText(key1.toLowerCase())).toBeInTheDocument();

    // Switch channel — key should be hidden again
    rerender(<ChannelInfoPane {...baseProps} channelKey={key2} channels={[ch1, ch2]} />);

    await waitFor(() => {
      expect(screen.getByText('Room2')).toBeInTheDocument();
    });
    expect(screen.queryByText(key2.toLowerCase())).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('channelInfo.showKey'))).toBeInTheDocument();
  });

  it('does not refetch the channel detail when the language changes', async () => {
    const key = 'FF'.repeat(16);
    const channel = makeChannel(key, 'Lingo', false);
    mockGetChannelDetail.mockReset();
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));
    const initialLanguage = i18n.language;

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);
    await screen.findByText('Lingo');

    try {
      await act(async () => {
        await i18n.changeLanguage(initialLanguage === 'fr' ? 'en' : 'fr');
      });
      expect(mockGetChannelDetail).toHaveBeenCalledTimes(1);
      expect(screen.getByText('Lingo')).toBeInTheDocument();
    } finally {
      await act(async () => {
        await i18n.changeLanguage(initialLanguage);
      });
    }
  });

  it('copies the official channel share URI, including region_scope', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const key = '8b3387e9c5cdea6ac9e5edbaa115cd72';
    const channel = {
      ...makeChannel(key, 'Public', false),
      flood_scope_override: '#Esperance',
    };
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('share.copyUri') }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(
        `meshcore://channel/add?name=Public&secret=${key}&region_scope=%23Esperance`
      );
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('share.showQr') }));
    expect(screen.getByTitle(i18n.t('share.qrTitle'))).toBeInTheDocument();
  });
});
