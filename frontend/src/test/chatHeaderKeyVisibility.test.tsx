import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChatHeader } from '../components/ChatHeader';
import i18n from '../i18n';
import sliceEn from '../i18n/locales/slices/b.en.json';
import sliceFr from '../i18n/locales/slices/b.fr.json';
import type { Channel, Contact, Conversation, PathDiscoveryResponse } from '../types';

i18n.addResourceBundle('en', 'translation', sliceEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceFr, true, true);
import { CONTACT_TYPE_ROOM } from '../types';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';

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

const noop = () => {};

const baseProps = {
  contacts: [],
  config: null,
  onTrace: noop,
  onPathDiscovery: vi.fn(async () => {
    throw new Error('unused');
  }) as (_: string) => Promise<PathDiscoveryResponse>,
  onToggleFavorite: noop,
  onSetChannelFloodScopeOverride: noop,
  onDeleteChannel: noop,
  onDeleteContact: noop,
};

describe('ChatHeader key visibility', () => {
  it('keeps keys out of the header — the info pane is where they live', () => {
    const channelKey = 'AA'.repeat(16);
    const channel = makeChannel(channelKey, '#general', true);
    const conversation: Conversation = { type: 'channel', id: channelKey, name: '#general' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.queryByText(channelKey.toLowerCase())).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t('chatHeader.showKey'))).not.toBeInTheDocument();
  });

  it('does not show a contact key or a reveal control beside the name', () => {
    const pubKey = '11'.repeat(32);
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[]} />);

    expect(screen.queryByText(pubKey)).not.toBeInTheDocument();
    expect(screen.queryByText(pubKey.slice(0, 12))).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t('chatHeader.showKey'))).not.toBeInTheDocument();
  });

  it('renders the clickable conversation title as a real button inside the heading', () => {
    const pubKey = '12'.repeat(32);
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };
    const onOpenContactInfo = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        onOpenContactInfo={onOpenContactInfo}
      />
    );

    const heading = screen.getByRole('heading', { name: /alice/i });
    const titleButton = within(heading).getByRole('button', {
      name: i18n.t('chatHeader.viewInfoFor', { name: 'Alice' }),
    });

    expect(heading).toContainElement(titleButton);
    fireEvent.click(titleButton);
    expect(onOpenContactInfo).toHaveBeenCalledWith(pubKey);
  });

  it('shows active regional override badge for channels', () => {
    const key = 'AB'.repeat(16);
    const channel = {
      ...makeChannel(key, '#flightless', true),
      flood_scope_override: '#Esperance',
    };
    const conversation: Conversation = { type: 'channel', id: key, name: '#flightless' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.getAllByText('#Esperance')).toHaveLength(2);
  });

  it('shows a filled push bell and toggles on click', () => {
    const conversation: Conversation = { type: 'contact', id: '11'.repeat(32), name: 'Alice' };
    const onTogglePush = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        pushSupported
        pushEnabledForConversation
        onTogglePush={onTogglePush}
      />
    );

    const bellBtn = screen.getByRole('button', { name: i18n.t('chatHeader.notifications') });
    expect(bellBtn).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(bellBtn);
    expect(onTogglePush).toHaveBeenCalledTimes(1);
  });

  it('mutes a channel from a dedicated bell-off button', () => {
    const key = 'AB'.repeat(16);
    const channel = makeChannel(key, '#flightless', true);
    const conversation: Conversation = { type: 'channel', id: key, name: '#flightless' };
    const onToggleMute = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[channel]}
        onToggleMute={onToggleMute}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: i18n.t('chatHeader.muteChannel') }));
    expect(onToggleMute).toHaveBeenCalledWith(key);
  });

  it('hides trace controls for room-server contacts but keeps the push bell', () => {
    const pubKey = '41'.repeat(32);
    const contact: Contact = {
      public_key: pubKey,
      name: 'Ops Board',
      type: CONTACT_TYPE_ROOM,
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
    };
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Ops Board' };

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        contacts={[contact]}
        pushSupported
        onTogglePush={vi.fn()}
      />
    );

    expect(
      screen.queryByRole('button', { name: i18n.t('chatHeader.pathDiscovery') })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('chatHeader.directTrace') })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('chatHeader.notifications') })
    ).toBeInTheDocument();
  });

  it('hides the delete button for the canonical Public channel', () => {
    const channel = makeChannel(PUBLIC_CHANNEL_KEY, 'Public', false);
    const conversation: Conversation = { type: 'channel', id: PUBLIC_CHANNEL_KEY, name: 'Public' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(
      screen.queryByRole('button', { name: i18n.t('chatHeader.delete') })
    ).not.toBeInTheDocument();
  });

  it('still shows the delete button for non-canonical channels named Public', () => {
    const key = 'AB'.repeat(16);
    const channel = makeChannel(key, 'Public', false);
    const conversation: Conversation = { type: 'channel', id: key, name: 'Public' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.getByRole('button', { name: i18n.t('chatHeader.delete') })).toBeInTheDocument();
  });

  it('opens path discovery modal for contacts and runs the request on demand', async () => {
    const pubKey = '21'.repeat(32);
    const contact: Contact = {
      public_key: pubKey,
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: 'AA',
      direct_path_len: 1,
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
    };
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };
    const onPathDiscovery = vi.fn().mockResolvedValue({
      contact,
      forward_path: { path: 'AA', path_len: 1, path_hash_mode: 0 },
      return_path: { path: '', path_len: 0, path_hash_mode: 0 },
    } satisfies PathDiscoveryResponse);

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        contacts={[contact]}
        onPathDiscovery={onPathDiscovery}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: i18n.t('chatHeader.pathDiscovery') }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('contactInfo.runDiscovery') }));

    await waitFor(() => {
      expect(onPathDiscovery).toHaveBeenCalledWith(pubKey);
    });
  });

  it('shows an override warning in the path discovery modal when forced routing is set', async () => {
    const pubKey = '31'.repeat(32);
    const contact: Contact = {
      public_key: pubKey,
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: 'AA',
      direct_path_len: 1,
      direct_path_hash_mode: 0,
      route_override_path: 'BBDD',
      route_override_len: 2,
      route_override_hash_mode: 0,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };

    render(
      <ChatHeader {...baseProps} conversation={conversation} channels={[]} contacts={[contact]} />
    );

    fireEvent.click(screen.getByRole('button', { name: i18n.t('chatHeader.pathDiscovery') }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('contactInfo.learnedRouteCurrent', { summary: '1 hop (AA)' }))
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('contactInfo.forcedRouteCurrent', { summary: '2 hops (BB -> DD)' }))
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('contactInfo.forcedWarning'))).toBeInTheDocument();
  });

  it('opens the regional override modal and applies the entered region', async () => {
    const key = 'CD'.repeat(16);
    const channel = makeChannel(key, '#flightless', true);
    const conversation: Conversation = { type: 'channel', id: key, name: '#flightless' };
    const onSetChannelFloodScopeOverride = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[channel]}
        onSetChannelFloodScopeOverride={onSetChannelFloodScopeOverride}
      />
    );

    fireEvent.click(screen.getByTitle(i18n.t('chatHeader.regionalOverride')));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(i18n.t('channelInfo.region')), {
      target: { value: 'Esperance' },
    });
    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('channelInfo.scopeTo', { name: '#flightless', region: 'Esperance' }),
      })
    );

    expect(onSetChannelFloodScopeOverride).toHaveBeenCalledWith(key, 'Esperance');
  });

  it('forces the channel unscoped via the modal (issue #303)', async () => {
    const key = 'CD'.repeat(16);
    const channel = makeChannel(key, '#flightless', true);
    const conversation: Conversation = { type: 'channel', id: key, name: '#flightless' };
    const onSetChannelFloodScopeOverride = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[channel]}
        onSetChannelFloodScopeOverride={onSetChannelFloodScopeOverride}
      />
    );

    fireEvent.click(screen.getByTitle(i18n.t('chatHeader.regionalOverride')));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('channelInfo.alwaysUnscoped', { name: '#flightless' }),
      })
    );

    expect(onSetChannelFloodScopeOverride).toHaveBeenCalledWith(key, '*');
  });
});
