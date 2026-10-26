import type { NotificationWatchedNode } from '../../../notifications/types';

export const WATCHED_MAX_NODES = 50;
export const WATCHED_DEFAULT_COOLDOWN_S = 3600;
export const WATCHED_DEFAULT_SILENCE_S = 86_400;
export const WATCHED_MAX_COOLDOWN_S = 30 * 86_400;
export const WATCHED_MIN_SILENCE_S = 60;
export const WATCHED_MAX_SILENCE_S = 365 * 86_400;
/** Web Push is decided per account: a watched node cannot name it (the server refuses it). */
export const WATCHED_EXCLUDED_CHANNEL = 'builtin:push';

export function findWatched(
  nodes: NotificationWatchedNode[],
  publicKey: string
): NotificationWatchedNode | undefined {
  const key = publicKey.toLowerCase();
  return nodes.find((n) => n.public_key.toLowerCase() === key);
}

/** A new entry with the defaults of the server. */
export function newWatched(publicKey: string, channels: string[]): NotificationWatchedNode {
  return {
    public_key: publicKey.toLowerCase(),
    channels: channels.filter((c) => c !== WATCHED_EXCLUDED_CHANNEL),
    cooldown_s: WATCHED_DEFAULT_COOLDOWN_S,
    silence_s: WATCHED_DEFAULT_SILENCE_S,
  };
}

/** The list after `node` replaced its entry (same key) or was appended. */
export function withWatched(
  nodes: NotificationWatchedNode[],
  node: NotificationWatchedNode
): NotificationWatchedNode[] {
  const at = nodes.findIndex((n) => n.public_key.toLowerCase() === node.public_key.toLowerCase());
  if (at < 0) return [...nodes, node];
  return nodes.map((n, i) => (i === at ? node : n));
}

export function withoutWatched(
  nodes: NotificationWatchedNode[],
  publicKey: string
): NotificationWatchedNode[] {
  const key = publicKey.toLowerCase();
  return nodes.filter((n) => n.public_key.toLowerCase() !== key);
}

/** Why a (cooldown minutes, silence hours) pair cannot be saved. */
export function watchedError(cooldownMin: number, silenceH: number): 'cooldown' | 'silence' | null {
  if (
    !Number.isInteger(cooldownMin) ||
    cooldownMin < 0 ||
    cooldownMin * 60 > WATCHED_MAX_COOLDOWN_S
  ) {
    return 'cooldown';
  }
  if (
    !Number.isInteger(silenceH) ||
    silenceH * 3600 < WATCHED_MIN_SILENCE_S ||
    silenceH * 3600 > WATCHED_MAX_SILENCE_S
  ) {
    return 'silence';
  }
  return null;
}
