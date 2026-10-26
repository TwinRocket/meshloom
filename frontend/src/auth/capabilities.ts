/**
 * What a role may do, as far as the interface is concerned (docs/dev/auth.md,
 * "Permissions"; `app/auth/permissions.py` is the authority).
 *
 * The server enforces every one of these on every request: hiding or disabling a
 * surface here only stops the interface offering what would be refused, it protects
 * nothing. `GET /auth/session` carries the role, and the role grants the permissions
 * (a cookie session has no token scope), so the table below mirrors `ROLE_PERMISSIONS`.
 */

import type { SettingsSection } from '../components/settings/settingsConstants';
import type { AuthRole } from './types';

export type Capability = 'read' | 'read_private' | 'send' | 'repeater_admin' | 'admin';

const ALL: readonly Capability[] = ['read', 'read_private', 'send', 'repeater_admin', 'admin'];

const ROLE_CAPABILITIES: Record<AuthRole, readonly Capability[]> = {
  kiosk: ['read'],
  viewer: ['read', 'read_private'],
  member: ['read', 'read_private', 'send'],
  admin: ALL,
};

/**
 * Settings sections a non-admin account may open: its own browser's preferences, and its
 * own profile and sessions (`own_account`). `access` shows the administrator parts only
 * to an admin (`SettingsAccessSection`).
 */
const ACCOUNT_SETTINGS_SECTIONS: readonly SettingsSection[] = ['local', 'access'];

/**
 * Settings sections opened by a role holding `send` (member and up): the plugin list and
 * statuses are member-readable (`GET /plugins`, `GET /plugins/pause`), every write stays
 * admin-only and the section hides it (`SettingsExtensionsSection`); in `share` a member
 * sees only "My uploads" (`GET /api/share/mine`, `DELETE /api/share/{id}`, both `send`),
 * never the settings (`SettingsShareSection`).
 */
const MEMBER_SETTINGS_SECTIONS: readonly SettingsSection[] = ['extensions', 'share'];

export interface Capabilities {
  /** The role these were derived from; null when nobody is known (tests, previews). */
  role: AuthRole | null;
  has: (capability: Capability) => boolean;
  /** Direct messages (`read_private`): the kiosk never sees one, the server never sends it. */
  canReadPrivate: boolean;
  /** The composer, and every action that sends on the air or edits a contact or channel. */
  canSend: boolean;
  /** Repeater and room administration panes (`repeater_admin`, held by `admin` only). */
  canAdministerRepeaters: boolean;
  /** Instance administration: fanout, backup, radio settings, debug snapshot, private key,
   * every plugin write and the global plugin pause... */
  isAdmin: boolean;
  /** Any settings page at all (the kiosk has none). */
  canOpenSettings: boolean;
  /** The caller's own profile and sessions (`own_account`: every role but the kiosk). */
  hasProfile: boolean;
  canOpenSettingsSection: (section: SettingsSection) => boolean;
}

export function capabilitiesOfRole(role: AuthRole): ReadonlySet<Capability> {
  return new Set(ROLE_CAPABILITIES[role] ?? []);
}

function build(role: AuthRole | null, granted: ReadonlySet<Capability>): Capabilities {
  const isAdmin = granted.has('admin');
  const canOpenSettingsSection = (section: SettingsSection) =>
    isAdmin ||
    (role !== null && role !== 'kiosk' && ACCOUNT_SETTINGS_SECTIONS.includes(section)) ||
    (granted.has('send') && MEMBER_SETTINGS_SECTIONS.includes(section));
  return {
    role,
    has: (capability) => granted.has(capability),
    canReadPrivate: granted.has('read_private'),
    canSend: granted.has('send'),
    canAdministerRepeaters: granted.has('repeater_admin'),
    isAdmin,
    canOpenSettings: role !== 'kiosk',
    hasProfile: role !== null && role !== 'kiosk',
    canOpenSettingsSection,
  };
}

export function capabilitiesFor(role: AuthRole): Capabilities {
  return build(role, capabilitiesOfRole(role));
}

/**
 * Outside a session provider (tests, previews) nothing is hidden: the server is the
 * authority, and an interface that offers too much only meets a coded refusal.
 */
export const UNRESTRICTED: Capabilities = {
  ...build('admin', new Set(ALL)),
  role: null,
};

/** The settings sections `capabilities` may open, in `order`. */
export function visibleSettingsSections(
  capabilities: Capabilities,
  order: readonly SettingsSection[]
): SettingsSection[] {
  return order.filter((section) => capabilities.canOpenSettingsSection(section));
}
