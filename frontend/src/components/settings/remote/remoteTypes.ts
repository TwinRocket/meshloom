/**
 * Types of `/api/remote` (`app/routers/remote.py`, docs/dev/remote.md). Kept out of
 * `types.ts`: only Settings → Remote access uses them.
 */

export type RemoteStateName = 'disabled' | 'unregistered' | 'registered';

/** `app/services/remote_relay.py` `LINK_STATES`; null while Remote is off. */
export const REMOTE_LINK_STATES = [
  'connecting',
  'connected',
  'reconnecting',
  'not_registered',
  'paused',
  'replaced',
  'banned',
  'update_required',
  'relay_paused',
] as const;

export type RemoteLinkStatus = (typeof REMOTE_LINK_STATES)[number];

export interface RemoteDevice {
  id: string;
  user_id: number;
  kind: 'browser' | 'client';
  label: string | null;
  origin: 'qr' | 'approval' | 'client';
  created_at: number;
  pass_expires_at: number;
  last_seen_at: number | null;
  revoked_at: number | null;
  /** Not revoked and in the current pass generation. */
  active: boolean;
}

export interface RemoteStatus {
  enabled: boolean;
  state: RemoteStateName;
  key_fingerprint: string | null;
  slug: string | null;
  hostname: string | null;
  relay_url: string | null;
  registered_at: number | null;
  last_error: string | null;
  relay_connected: boolean;
  /** A `REMOTE_LINK_STATES` value; any other string is shown as unknown. */
  link_status: string | null;
  gate: { gen: number; revoked: number };
  devices: RemoteDevice[];
}

export interface RemoteEnrollTicket {
  /** The QR code's content; the token is in the fragment. Shown once, never stored. */
  url: string;
  expires_at: number;
}

export interface RemoteAccessRequest {
  request_id: string;
  /** What the new device shows (XXXXX-XXXXX). */
  code: string;
  /** Sent by the unknown device: untrusted text. */
  user_agent: string;
  created_at: number;
  expires_at: number;
}

export interface RemoteGateResult {
  gate_pushed: boolean;
}
