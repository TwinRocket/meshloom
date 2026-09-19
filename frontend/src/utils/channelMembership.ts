import type { Channel } from '../types';

export function isPendingChannel(channel: Channel | null | undefined): boolean {
  return channel?.membership === 'pending';
}

export function adoptedChannels(channels: Channel[]): Channel[] {
  return channels.filter((channel) => channel.membership !== 'pending');
}

export function pendingChannels(channels: Channel[]): Channel[] {
  return channels.filter((channel) => channel.membership === 'pending');
}
