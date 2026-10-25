import type { PluginDiagnostic, PluginInstance } from './pluginsTypes';

/** Statuses a user can never start from here: only an update leaves them. */
const FROZEN_STATUSES = new Set(['revoked', 'incompatible']);

export type StatusTone = 'ok' | 'warn' | 'bad' | 'muted';

export const KNOWN_STATUSES = [
  'installed',
  'disabled',
  'starting',
  'running',
  'paused',
  'error',
  'crash_loop',
  'needs_attention',
  'incompatible',
  'revoked',
] as const;

export function statusTone(status: string): StatusTone {
  switch (status) {
    case 'running':
      return 'ok';
    case 'starting':
    case 'installed':
    case 'needs_attention':
    case 'paused':
      return 'warn';
    case 'error':
    case 'crash_loop':
    case 'incompatible':
    case 'revoked':
      return 'bad';
    default:
      return 'muted';
  }
}

/** `revoked` and `incompatible` instances never start (the API answers 409 too). */
export function isFrozen(instance: Pick<PluginInstance, 'status'>): boolean {
  return FROZEN_STATUSES.has(instance.status);
}

/** The typed reason an instance waits for the user, or null. */
export function attentionType(instance: Pick<PluginInstance, 'attention'>): string | null {
  const type = instance.attention?.type;
  return typeof type === 'string' ? type : null;
}

/**
 * The i18n key under `settings.extensions.attention` that explains why the instance waits.
 * A bundled update held only because its privacy declaration widened says so
 * (`attention.privacy_widened` with no added permission).
 */
export function attentionTextKey(instance: Pick<PluginInstance, 'attention'>): string | null {
  const type = attentionType(instance);
  if (type !== 'review_permissions' || instance.attention?.privacy_widened !== true) return type;
  const added = instance.attention?.added;
  return Array.isArray(added) && added.length > 0
    ? 'review_permissions_privacy'
    : 'privacy_widened';
}

/**
 * The bare `last_error` codes the host itself writes (`app/plugins/host.py`). Only these
 * are translated: any other value, snake_case or not, may come from the plugin and is
 * shown as written (React escapes it), so a plugin cannot pass its text off as a host
 * message.
 */
export const HOST_LAST_ERROR_CODES: readonly string[] = ['plugin_code_mismatch'];

/**
 * The text of an instance's `last_error`: a host code (`HOST_LAST_ERROR_CODES`) is
 * translated through `errors.<code>`, anything else is shown as the server wrote it.
 */
export function lastErrorText(
  t: (key: string, options?: Record<string, unknown>) => string,
  error: string
): string {
  return HOST_LAST_ERROR_CODES.includes(error)
    ? t(`errors.${error}`, { defaultValue: error })
    : error;
}

/** The required config fields the instance still lacks (`attention.fields`). */
export function attentionFields(instance: Pick<PluginInstance, 'attention'>): string[] {
  const fields = instance.attention?.fields;
  return Array.isArray(fields) ? fields.filter((f): f is string => typeof f === 'string') : [];
}

/**
 * An instance restored from a backup without its code (`attention.type = "reinstall"`):
 * it never starts until reinstalled (the API answers 409 `plugin_reinstall_required`).
 */
export function needsReinstall(instance: Pick<PluginInstance, 'status' | 'attention'>): boolean {
  return instance.status === 'needs_attention' && attentionType(instance) === 'reinstall';
}

/**
 * Why the toggle cannot enable the instance from the list, or null when it can.
 * `review` means the drawer's explicit acknowledgement is needed first.
 */
export function enableBlock(
  instance: PluginInstance
): 'frozen' | 'config_required' | 'reinstall' | 'review' | null {
  if (isFrozen(instance)) return 'frozen';
  if (instance.status !== 'needs_attention') return null;
  if (needsReinstall(instance)) return 'reinstall';
  return attentionType(instance) === 'config_required' ? 'config_required' : 'review';
}

/** The manifest's declared permissions of an instance. */
export function declaredPermissions(
  instance: Pick<PluginInstance, 'manifest'>
): Record<string, Record<string, unknown>> {
  const permissions = (instance.manifest as { permissions?: unknown }).permissions;
  return permissions && typeof permissions === 'object'
    ? (permissions as Record<string, Record<string, unknown>>)
    : {};
}

export function localizedText(map: Record<string, string> | undefined, language: string): string {
  if (!map) return '';
  const short = language.toLowerCase().split('-')[0];
  return map[language] ?? map[short] ?? map.en ?? Object.values(map)[0] ?? '';
}

/** The i18n key of a permission (`messages.read` -> `plugins.permissions.messages_read`). */
export function permissionKey(name: string): string {
  return `plugins.permissions.${name.replace('.', '_')}`;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((h): h is string => typeof h === 'string') : [];
}

/** The public hosts of the `http` permission (HTTPS only). */
export function httpHosts(permissions: Record<string, Record<string, unknown>>): string[] {
  return stringList(permissions.http?.hosts);
}

/** The devices of the user's local network the `http` permission names (`lan_hosts`). */
export function httpLanHosts(permissions: Record<string, Record<string, unknown>>): string[] {
  return stringList(permissions.http?.lan_hosts);
}

/** `http` params from both lists, an empty list left out like the server does. */
export function httpParams(hosts: string[], lanHosts: string[]): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  if (hosts.length > 0 || lanHosts.length === 0) params.hosts = hosts;
  if (lanHosts.length > 0) params.lan_hosts = lanHosts;
  return params;
}

/**
 * True when the plugin can read private messages (`direct` or every channel) and talk
 * to the internet: the consent then carries the dedicated exfiltration sentence.
 */
export function readsPrivateAndConnects(
  permissions: Record<string, Record<string, unknown>>
): boolean {
  const read = permissions['messages.read'];
  if (!read || httpHosts(permissions).length + httpLanHosts(permissions).length === 0) {
    return false;
  }
  return read.direct === true || read.channels === 'all';
}

export interface PrivacyDeclaration {
  sendsTo: string[];
  storesContent: boolean;
}

export function privacyOf(manifest: Record<string, unknown>): PrivacyDeclaration | null {
  const privacy = manifest.privacy as
    { sends_to_third_parties?: unknown; stores_message_content?: unknown } | undefined;
  if (!privacy || typeof privacy !== 'object') return null;
  const sends = Array.isArray(privacy.sends_to_third_parties)
    ? privacy.sends_to_third_parties.filter((h): h is string => typeof h === 'string')
    : [];
  return { sendsTo: sends, storesContent: privacy.stores_message_content === true };
}

/** A name the user can read: the instance label, falling back to the plugin id. */
export function displayName(instance: Pick<PluginInstance, 'label' | 'plugin_id'>): string {
  return instance.label || instance.plugin_id;
}

export function sortInstances(list: PluginInstance[]): PluginInstance[] {
  return [...list].sort((a, b) => a.sort_order - b.sort_order || a.installed_at - b.installed_at);
}

/** A plain-text block to paste in a report (never holds a config value or a secret). */
export function formatDiagnostic(d: PluginDiagnostic): string {
  return [
    `plugin: ${d.plugin_id} ${d.plugin_version} (${d.kind})`,
    `meshloom: ${d.meshloom_version}`,
    `python: ${d.python_version}`,
    `isolation: ${d.isolation}`,
    `status: ${d.status}`,
    `last_error: ${d.last_error ?? '-'}`,
    `restarts: ${d.restarts}`,
    `dropped_events: ${d.dropped_events}`,
    `permissions: ${d.granted_permissions.join(', ') || '-'}`,
    '--- logs ---',
    ...d.logs,
  ].join('\n');
}

export const MAX_SEND_PER_HOUR = 60;
export const MAX_SEND_PER_DAY = 500;

/** The `messages.send` quota the instance runs with now, and the ceiling it may be lowered to. */
export function sendQuotaState(instance: PluginInstance): {
  perHour: number;
  perDay: number;
  maxHour: number;
  maxDay: number;
} {
  const declared = declaredPermissions(instance)['messages.send'] ?? {};
  const stored = (instance.quotas['messages.send'] ?? {}) as Record<string, unknown>;
  const num = (value: unknown, fallback: number) =>
    typeof value === 'number' && Number.isInteger(value) ? value : fallback;
  const maxHour = Math.min(num(declared.per_hour, MAX_SEND_PER_HOUR), MAX_SEND_PER_HOUR);
  const maxDay = Math.min(num(declared.per_day, MAX_SEND_PER_DAY), MAX_SEND_PER_DAY);
  return {
    perHour: Math.min(num(stored.per_hour, maxHour), maxHour),
    perDay: Math.min(num(stored.per_day, maxDay), maxDay),
    maxHour,
    maxDay,
  };
}
