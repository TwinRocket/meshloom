import type { TFunction } from 'i18next';

import type { Channel, Contact } from '../../types';
import { isPendingChannel } from '../../utils/channelMembership';
import type { FeedEvent } from '../../utils/feedEvents';
import { getPubkeyPrefix } from '../../utils/pubkey';

/** What an event row says and offers, resolved from the lists the client holds right now. */
export interface FeedEventView {
  sentence: string;
  /** Contact sheet target (first sighting, rename). */
  contactKey: string | null;
  /** A discovered channel still waiting for adopt / refuse. */
  pendingChannelKey: string | null;
  /** The event is about the radio's own state. */
  radio: boolean;
}

/** `m min s s` style duration in the UI language, from whole seconds. */
export function formatOutage(language: string, seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  const unit = (value: number, name: 'hour' | 'minute' | 'second') =>
    new Intl.NumberFormat(language, { style: 'unit', unit: name, unitDisplay: 'short' }).format(
      value
    );
  const hours = Math.floor(minutes / 60);
  const parts: string[] = [];
  if (hours > 0) parts.push(unit(hours, 'hour'));
  if (minutes % 60 > 0) parts.push(unit(minutes % 60, 'minute'));
  if (hours === 0 && (rest > 0 || parts.length === 0)) parts.push(unit(rest, 'second'));
  return parts.join(' ');
}

function contactLabel(key: string, fallback: string | null, contacts: readonly Contact[]): string {
  const live = contacts.find((c) => c.public_key.toLowerCase() === key.toLowerCase());
  return live?.name || fallback || getPubkeyPrefix(key);
}

/** One translated sentence per event id, names from the current lists, then the frame's own. */
export function describeFeedEvent(
  event: FeedEvent,
  t: TFunction,
  language: string,
  contacts: readonly Contact[],
  channels: readonly Channel[]
): FeedEventView {
  const base = { contactKey: null, pendingChannelKey: null, radio: false };
  switch (event.id) {
    case 'radio.offline':
      return { ...base, radio: true, sentence: t('feed.events.radioOffline') };
    case 'radio.online':
      return {
        ...base,
        radio: true,
        sentence: t('feed.events.radioOnline', {
          duration: formatOutage(language, event.downtimeS),
        }),
      };
    case 'contact.first_seen':
      return {
        ...base,
        contactKey: event.publicKey,
        sentence: t('feed.events.contactFirstSeen', {
          name: contactLabel(event.publicKey, event.name, contacts),
        }),
      };
    case 'contact.renamed':
      return {
        ...base,
        contactKey: event.publicKey,
        sentence: t('feed.events.contactRenamed', { from: event.from, to: event.to }),
      };
    case 'channel.found': {
      const live = channels.find((c) => c.key.toUpperCase() === event.channelKey.toUpperCase());
      return {
        ...base,
        pendingChannelKey: live && isPendingChannel(live) ? live.key : null,
        sentence: t('feed.events.channelFound', { name: live?.name || event.name }),
      };
    }
  }
}
