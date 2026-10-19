import type { TFunction } from 'i18next';

import {
  reportAuthRequired,
  reportEnrolmentRequired,
  reportForbidden,
  reportPasswordChangeRequired,
} from './auth/authEvents';
import type {
  BundledPlugin,
  LocalPluginCreate,
  PluginCheckResult,
  PluginDiagnostic,
  PluginInstallRequest,
  PluginLogs,
  PluginPatch,
  PluginSourceResult,
  PluginTestRequest,
  PluginTestResult,
} from './components/settings/extensions/pluginsTypes';
import type {
  NotificationChannel,
  NotificationChannelCreate,
  NotificationChannelPatch,
  NotificationDeliveries,
  NotificationEventInfo,
  NotificationRules,
  NotificationRulesPatch,
  NotificationTypeInfo,
} from './notifications/types';
import type {
  AuthAuditPage,
  AuthLoginRequest,
  AuthMe,
  AuthSessionRow,
  AuthPairingCreate,
  AuthPairingCreated,
  AuthPairingRow,
  AuthTokenCreate,
  AuthTokenCreated,
  AuthTokenRow,
  AuthUserCreate,
  AuthUserPatch,
  AuthUserRow,
  AuthSessionInfo,
  AuthSettings,
  AuthSetupRequest,
  PasskeyRow,
  RecoveryCodes,
  TotpEnrolment,
  TotpStatus,
} from './auth/types';
import { edgeSessionLost, isEdgeRedirect, reportEdgeSessionExpired } from './utils/edgeSession';

import type {
  AppSettings,
  AppSettingsView,
  AppSettingsUpdate,
  BackupExport,
  BackupInspectResult,
  BackupRestoreJob,
  BackupRestoreResult,
  BackupRestoreV2Request,
  ContactGroup,
  BulkCreateHashtagChannelsResult,
  Channel,
  ChannelDetail,
  RejectedChannel,
  CommandResponse,
  Contact,
  ContactAnalytics,
  ContactAdvertPathSummary,
  DirectoryMapNodesQuery,
  DirectoryMapNodesResponse,
  DirectoryNeighborsResponse,
  DirectoryNodeSearchResponse,
  DirectoryReachResponse,
  DirectoryResolveHopsResponse,
  PacketObserverReachCountsResponse,
  PacketObserverReachResponse,
  LocateResponse,
  ContactTelemetryResponse,
  FanoutConfig,
  GroupTextSamplesResponse,
  HealthStatus,
  RawPacketHistoryQuery,
  RawPacketHistoryResponse,
  MaintenanceResult,
  MeshTestResponse,
  Message,
  MessagesAroundResponse,
  RawPacket,
  RadioAdvertMode,
  RadioBleDeviceInfo,
  RadioBlePairingStatus,
  RadioConfig,
  RadioConfigUpdate,
  RadioCustomVars,
  RadioWifiChangeResult,
  RadioWifiCredentialsRequest,
  RadioWifiEnabledRequest,
  RadioWifiPromptDismissRequest,
  RadioWifiState,
  RadioFactoryResetResponse,
  RadioSelfTelemetry,
  RadioIdentityActionResponse,
  RadioReconnectResponse,
  RadioProxyStatus,
  RadioProxyUpdate,
  RadioTransportConfig,
  RadioTransportUpdate,
  RadioDiscoveryResponse,
  RadioRegionDiscoveryResponse,
  RadioRegionVerifyResponse,
  RadioTraceHopRequest,
  RadioTraceResponse,
  RadioDiscoveryTarget,
  PathDiscoveryResponse,
  NotificationDestinationChannel,
  PushDefaultsPatch,
  ConversationMediaOverride,
  PushPreferences,
  PushSubscriptionInfo,
  TelemetryAlertCatalog,
  ResendChannelMessageResponse,
  RepeaterAclResponse,
  RepeaterAdvertIntervalsResponse,
  RepeaterLoginResponse,
  RepeaterLppTelemetryResponse,
  RepeaterNeighborsResponse,
  RepeaterNodeInfoResponse,
  RepeaterOwnerInfoResponse,
  RepeaterRadioSettingsResponse,
  RepeaterRegionsResponse,
  RepeaterStatusResponse,
  TelemetryHistoryEntry,
  TelemetrySchedule,
  TrackedTelemetryContactsResponse,
  TrackedTelemetryResponse,
  StatisticsResponse,
  CommunityHashtagsResponse,
  CommunityIataBindRequest,
  CommunityIataBindResult,
  CommunityIataActiveResponse,
  CommunityLiveStatus,
  CommunityMeStats,
  CommunityPublicStats,
  CommunityAirportHit,
  CommunityIataSuggestion,
  CommunityStatus,
  CommunityUpdate,
  OssUpdateSettingsPatch,
  OssUpdateStatus,
  TraceResponse,
  UnreadCounts,
  ShareLink,
  ShareMeta,
  PluginInstance,
  ShareStatus,
} from './types';

const API_BASE = './api';

/** Error thrown by API calls, carrying the HTTP status so callers can tell
 * retryable failures from ones the mesh already answered (e.g. 422 timeouts). */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: unknown = undefined,
    /** Seconds the server asked us to wait (429), from `Retry-After` or `params.retry_after`. */
    public readonly retryAfter: number | null = null
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function getApiErrorCode(
  detail: unknown
): { code: string; params: Record<string, unknown> } | null {
  if (!detail || typeof detail !== 'object') {
    return null;
  }
  const record = detail as { code?: unknown; params?: unknown };
  if (typeof record.code !== 'string' || record.code.length === 0) {
    return null;
  }
  const params =
    record.params && typeof record.params === 'object' && !Array.isArray(record.params)
      ? (record.params as Record<string, unknown>)
      : {};
  return { code: record.code, params };
}

/** Translate a known API error code, otherwise keep the backend message. */
export function formatApiError(err: unknown, t: TFunction): string {
  if (err instanceof ApiError) {
    const parsed = getApiErrorCode(err.detail);
    if (parsed) {
      const key = `errors.${parsed.code}`;
      const retryAfter = err.retryAfter ?? undefined;
      const translated = t(key, { retry_after: retryAfter, ...parsed.params });
      if (translated !== key) {
        return translated;
      }
    }
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const hasBody = options?.body !== undefined;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${url}`, {
      ...options,
      headers: {
        ...(hasBody && { 'Content-Type': 'application/json' }),
        ...options?.headers,
      },
    });
  } catch (err) {
    // A cancelled request says nothing about the session: skip the probe (and its extra
    // network round trip) and let the caller handle the abort.
    if (isAbortError(err)) throw err;
    // An authentication proxy refusing a JSON request throws the same way an
    // unreachable server does, so ask before blaming the network.
    if (await edgeSessionLost()) reportEdgeSessionExpired();
    throw err;
  }
  // A response from another origin is the proxy's login page, whatever its status.
  if (isEdgeRedirect(res)) {
    reportEdgeSessionExpired();
    throw new ApiError('Authentication required', res.status);
  }
  if (!res.ok) {
    const error = await apiErrorFrom(res);
    // The account is gone (expired session, or no account yet): the gate takes over.
    // Login and setup answer their own 401, which is a form error and not a lost session.
    if (error.status === 401 && !AUTH_FORM_PATHS.has(url)) {
      reportAuthRequired(getApiErrorCode(error.detail)?.code ?? null);
    }
    // The role may not do this: the session layer decides whether to tell the reader.
    const refusal = error.status === 403 ? getApiErrorCode(error.detail) : null;
    if (refusal?.code === 'auth_password_change_required') reportPasswordChangeRequired();
    if (refusal?.code === 'auth_2fa_enrolment_required') reportEnrolmentRequired();
    if (refusal?.code === 'auth_forbidden') {
      const permission = refusal.params.permission;
      reportForbidden({
        method: options?.method ?? 'GET',
        permission: typeof permission === 'string' ? permission : null,
      });
    }
    throw error;
  }
  // 204 No Content (revoking a token) has no body to parse.
  if (res.status === 204) return undefined as T;
  return res.json();
}

const AUTH_FORM_PATHS = new Set([
  '/auth/login',
  '/auth/login/2fa',
  '/auth/setup',
  // The passkey sign-in answers its own 401 (a wrong assertion, an expired pending login).
  '/auth/webauthn/login/options',
  '/auth/webauthn/login/verify',
]);

/** Seconds from a `Retry-After` header (delta-seconds form), or null. */
function retryAfterSeconds(header: string | null, detail: unknown): number | null {
  const fromParams = Number(getApiErrorCode(detail)?.params.retry_after);
  if (Number.isFinite(fromParams) && fromParams > 0) return Math.ceil(fromParams);
  const fromHeader = header === null ? NaN : Number(header);
  return Number.isFinite(fromHeader) && fromHeader > 0 ? Math.ceil(fromHeader) : null;
}

/** The ApiError for a failed response (FastAPI's {"detail": ...} unwrapped). */
async function apiErrorFrom(res: Response): Promise<ApiError> {
  const errorText = await res.text();
  // FastAPI returns errors as {"detail": "message"}, extract the message
  let errorMessage = errorText || res.statusText;
  let detail: unknown = errorText;
  try {
    const errorJson = JSON.parse(errorText);
    if (errorJson.detail !== undefined) {
      detail = errorJson.detail;
      errorMessage =
        typeof errorJson.detail === 'string'
          ? errorJson.detail
          : errorJson.detail?.message || errorJson.detail?.reason || errorText;
    }
  } catch {
    // Not JSON, use raw text
  }
  const retryAfter =
    res.status === 429 ? retryAfterSeconds(res.headers?.get('Retry-After') ?? null, detail) : null;
  return new ApiError(errorMessage, res.status, detail, retryAfter);
}

/** The file name a Content-Disposition header proposes (RFC 5987 first). */
export function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const extended = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      // fall through to the ASCII name
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header);
  return plain ? plain[1] : fallback;
}

/** Check if an error is an AbortError (request was cancelled) */
export function isAbortError(err: unknown): boolean {
  // DOMException is thrown by fetch when aborted, and it's not an Error subclass
  if (err instanceof DOMException && err.name === 'AbortError') {
    return true;
  }
  // Also check for Error with AbortError name (for compatibility)
  return err instanceof Error && err.name === 'AbortError';
}

interface DecryptResult {
  started: boolean;
  total_packets: number;
  message: string;
}

export const api = {
  // Auth (public routes: they answer before anyone is signed in)
  getAuthSession: () => fetchJson<AuthSessionInfo>('/auth/session', { cache: 'no-store' }),
  authSetup: (body: AuthSetupRequest) =>
    fetchJson<AuthSessionInfo>('/auth/setup', { method: 'POST', body: JSON.stringify(body) }),
  authLogin: (body: AuthLoginRequest) =>
    fetchJson<AuthSessionInfo>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  /** `POST /auth/login/2fa`: the second step, the pending token of the first and a code. */
  authLogin2fa: (
    body: { pending_token: string; code: string } | { pending_token: string; passkey: object }
  ) =>
    fetchJson<AuthSessionInfo>('/auth/login/2fa', { method: 'POST', body: JSON.stringify(body) }),
  /** Passkeys (`/auth/webauthn/*`): options and answers are the browser's JSON, passed through. */
  passkeyLoginOptions: (pending_token?: string) =>
    fetchJson<object>('/auth/webauthn/login/options', {
      method: 'POST',
      body: JSON.stringify(pending_token ? { pending_token } : {}),
    }),
  passkeyLoginVerify: (credential: object) =>
    fetchJson<AuthSessionInfo>('/auth/webauthn/login/verify', {
      method: 'POST',
      body: JSON.stringify({ credential }),
    }),
  passkeyRegisterOptions: (password: string, code?: string) =>
    fetchJson<object>('/auth/webauthn/register/options', {
      method: 'POST',
      body: JSON.stringify(code ? { password, code } : { password }),
    }),
  passkeyRegisterVerify: (credential: object, name: string) =>
    fetchJson<PasskeyRow>('/auth/webauthn/register/verify', {
      method: 'POST',
      body: JSON.stringify({ credential, name }),
    }),
  listPasskeys: () => fetchJson<PasskeyRow[]>('/auth/webauthn/credentials', { cache: 'no-store' }),
  deletePasskey: (id: number, password: string) =>
    fetchJson<{ ok: boolean }>(`/auth/webauthn/credentials/${id}`, {
      method: 'DELETE',
      body: JSON.stringify({ password }),
    }),
  getTotpStatus: () => fetchJson<TotpStatus>('/auth/2fa', { cache: 'no-store' }),
  enrolTotp: (password: string) =>
    fetchJson<TotpEnrolment>('/auth/2fa/enrol', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  confirmTotp: (code: string) =>
    fetchJson<RecoveryCodes>('/auth/2fa/confirm', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  disableTotp: (password: string, code: string) =>
    fetchJson<{ ok: boolean }>('/auth/2fa/disable', {
      method: 'POST',
      body: JSON.stringify({ password, code }),
    }),
  regenerateRecoveryCodes: (password: string, code: string) =>
    fetchJson<RecoveryCodes>('/auth/2fa/recovery', {
      method: 'POST',
      body: JSON.stringify({ password, code }),
    }),
  authLogout: () => fetchJson<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  /** `PATCH /auth/me`: the one write open while a password change is required. */
  changeOwnPassword: (current_password: string, new_password: string) =>
    fetchJson<unknown>('/auth/me', {
      method: 'PATCH',
      body: JSON.stringify({ current_password, new_password }),
    }),
  getAuthMe: () => fetchJson<AuthMe>('/auth/me', { cache: 'no-store' }),
  patchAuthMe: (patch: { display_name: string }) =>
    fetchJson<AuthMe>('/auth/me', { method: 'PATCH', body: JSON.stringify(patch) }),
  listAuthSessions: () => fetchJson<AuthSessionRow[]>('/auth/sessions', { cache: 'no-store' }),
  revokeAuthSession: (id: string) =>
    fetchJson<{ ok: boolean }>(`/auth/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** Every session but the current one. */
  revokeOtherAuthSessions: () =>
    fetchJson<{ revoked: number }>('/auth/sessions', { method: 'DELETE' }),
  // API and device tokens (own account; an admin lists everyone's)
  listAuthTokens: () => fetchJson<AuthTokenRow[]>('/auth/tokens', { cache: 'no-store' }),
  createAuthToken: (body: AuthTokenCreate) =>
    fetchJson<AuthTokenCreated>('/auth/tokens', { method: 'POST', body: JSON.stringify(body) }),
  revokeAuthToken: (id: string) =>
    fetchJson<void>(`/auth/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // Device pairing codes (admin, own session)
  listAuthPairings: () => fetchJson<AuthPairingRow[]>('/auth/pairings', { cache: 'no-store' }),
  createAuthPairing: (body: AuthPairingCreate) =>
    fetchJson<AuthPairingCreated>('/auth/pairings', { method: 'POST', body: JSON.stringify(body) }),
  revokeAuthPairing: (id: string) =>
    fetchJson<void>(`/auth/pairings/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // Users (admin)
  listAuthUsers: () => fetchJson<AuthUserRow[]>('/auth/users', { cache: 'no-store' }),
  createAuthUser: (body: AuthUserCreate) =>
    fetchJson<AuthUserRow>('/auth/users', { method: 'POST', body: JSON.stringify(body) }),
  patchAuthUser: (id: number, patch: AuthUserPatch) =>
    fetchJson<AuthUserRow>(`/auth/users/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteAuthUser: (id: number) =>
    fetchJson<{ ok: boolean }>(`/auth/users/${id}`, { method: 'DELETE' }),
  setAuthUserPassword: (id: number, password: string, must_change_password: boolean) =>
    fetchJson<AuthUserRow>(`/auth/users/${id}/password`, {
      method: 'POST',
      body: JSON.stringify({ password, must_change_password }),
    }),
  revokeAuthUserSessions: (id: number) =>
    fetchJson<{ revoked: number }>(`/auth/users/${id}/sessions`, { method: 'DELETE' }),
  /** `GET /auth/audit` (admin): one page of the audit log, newest first; `cursor` is the previous `next_cursor`. */
  getAuthAudit: (cursor?: string | null, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor) params.set('cursor', cursor);
    return fetchJson<AuthAuditPage>(`/auth/audit?${params}`, { cache: 'no-store' });
  },
  getAuthSettings: () => fetchJson<AuthSettings>('/auth/settings', { cache: 'no-store' }),
  patchAuthSettings: (patch: Partial<AuthSettings>) =>
    fetchJson<AuthSettings>('/auth/settings', { method: 'PATCH', body: JSON.stringify(patch) }),

  // Health
  getHealth: () => fetchJson<HealthStatus>('/health'),
  getUpdates: () => fetchJson<OssUpdateStatus>('/updates'),
  applyUpdate: () => fetchJson<OssUpdateStatus>('/updates/apply', { method: 'POST' }),
  refreshUpdates: () => fetchJson<OssUpdateStatus>('/updates/refresh', { method: 'POST' }),
  patchUpdateSettings: (settings: OssUpdateSettingsPatch) =>
    fetchJson<OssUpdateStatus>('/updates/settings', {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }),

  // Radio config
  getRadioConfig: () => fetchJson<RadioConfig>('/radio/config'),
  updateRadioConfig: (config: RadioConfigUpdate) =>
    fetchJson<RadioConfig>('/radio/config', {
      method: 'PATCH',
      body: JSON.stringify(config),
    }),
  getRadioCustomVars: () => fetchJson<RadioCustomVars>('/radio/custom-vars'),
  setRadioCustomVar: (name: string, value: string) =>
    fetchJson<RadioCustomVars>(`/radio/custom-vars/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  /** Cached Wi-Fi state; `refresh` reads it live (the full check when none completed). */
  getRadioWifi: (refresh = false) =>
    fetchJson<RadioWifiState>(refresh ? '/radio/wifi?refresh=true' : '/radio/wifi'),
  setRadioWifiCredentials: (body: RadioWifiCredentialsRequest) =>
    fetchJson<RadioWifiChangeResult>('/radio/wifi/credentials', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  setRadioWifiEnabled: (body: RadioWifiEnabledRequest) =>
    fetchJson<RadioWifiChangeResult>('/radio/wifi/enabled', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  dismissRadioWifiPrompt: (body: RadioWifiPromptDismissRequest) =>
    fetchJson<RadioWifiState>('/radio/wifi/prompt/dismiss', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  /** Reboot to apply Wi-Fi settings (a planned `wifi_config` reboot, not `/radio/reboot`). */
  rebootRadioForWifi: () =>
    fetchJson<RadioWifiChangeResult>('/radio/wifi/reboot', { method: 'POST' }),
  getRadioSelfTelemetry: () => fetchJson<RadioSelfTelemetry>('/radio/self-telemetry'),
  factoryResetRadio: (confirmName: string) =>
    fetchJson<RadioFactoryResetResponse>('/radio/factory-reset', {
      method: 'POST',
      body: JSON.stringify({ confirm_name: confirmName }),
    }),
  getPrivateKey: () => fetchJson<{ private_key: string }>('/radio/private-key'),
  setPrivateKey: (privateKey: string) =>
    fetchJson<{ status: string }>('/radio/private-key', {
      method: 'PUT',
      body: JSON.stringify({ private_key: privateKey }),
    }),
  sendAdvertisement: (mode: RadioAdvertMode = 'flood') =>
    fetchJson<{ status: string }>('/radio/advertise', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
  discoverMesh: (target: RadioDiscoveryTarget) =>
    fetchJson<RadioDiscoveryResponse>('/radio/discover', {
      method: 'POST',
      body: JSON.stringify({ target }),
    }),
  /** Checks candidate names against stored packets. Works with the radio off. */
  verifyRegions: (names?: string[], hours?: number) =>
    fetchJson<RadioRegionVerifyResponse>('/radio/regions/verify', {
      method: 'POST',
      body: JSON.stringify({
        ...(names && names.length > 0 ? { names } : {}),
        ...(hours ? { hours } : {}),
      }),
    }),
  discoverRegions: (publicKeys?: string[]) =>
    fetchJson<RadioRegionDiscoveryResponse>('/radio/discover-regions', {
      method: 'POST',
      body: JSON.stringify(publicKeys && publicKeys.length > 0 ? { public_keys: publicKeys } : {}),
    }),
  requestRadioTrace: (hopHashBytes: 1 | 2 | 4, hops: RadioTraceHopRequest[]) =>
    fetchJson<RadioTraceResponse>('/radio/trace', {
      method: 'POST',
      body: JSON.stringify({ hop_hash_bytes: hopHashBytes, hops }),
    }),
  rebootRadio: () =>
    fetchJson<{ status: string; message: string }>('/radio/reboot', {
      method: 'POST',
    }),
  disconnectRadio: () =>
    fetchJson<{ status: string; message: string; connected: boolean; paused: boolean }>(
      '/radio/disconnect',
      {
        method: 'POST',
      }
    ),
  reconnectRadio: () =>
    fetchJson<RadioReconnectResponse>('/radio/reconnect', {
      method: 'POST',
    }),
  getRadioTransport: () => fetchJson<RadioTransportConfig>('/radio/transport'),
  updateRadioTransport: (body: RadioTransportUpdate) =>
    fetchJson<RadioTransportConfig>('/radio/transport', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  /** Restore the transport saved before the last change, on the server (BLE PIN kept). */
  revertRadioTransport: () =>
    fetchJson<RadioTransportConfig>('/radio/transport/revert', { method: 'POST' }),
  getRadioProxy: () => fetchJson<RadioProxyStatus>('/radio/proxy'),
  updateRadioProxy: (body: RadioProxyUpdate) =>
    fetchJson<RadioProxyStatus>('/radio/proxy', {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  scanRadioBle: () =>
    fetchJson<{ devices: RadioBleDeviceInfo[] }>('/radio/transport/ble-scan', {
      method: 'POST',
    }),
  getRadioBlePairing: () => fetchJson<RadioBlePairingStatus>('/radio/transport/ble-pairing'),
  pairRadioBle: () =>
    fetchJson<RadioBlePairingStatus>('/radio/transport/ble-pairing', { method: 'POST' }),
  forgetRadioBlePairing: () =>
    fetchJson<RadioBlePairingStatus>('/radio/transport/ble-pairing', { method: 'DELETE' }),
  adoptRadioIdentity: (body: { confirm_wipe: boolean }) =>
    fetchJson<RadioIdentityActionResponse>('/radio/identity/adopt', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  rejectRadioIdentity: () =>
    fetchJson<RadioIdentityActionResponse>('/radio/identity/reject', {
      method: 'POST',
    }),

  // Contacts
  getContacts: (limit = 100, offset = 0) =>
    fetchJson<Contact[]>(`/contacts?limit=${limit}&offset=${offset}`),
  getRepeaterAdvertPaths: (limitPerRepeater = 10) =>
    fetchJson<ContactAdvertPathSummary[]>(
      `/contacts/repeaters/advert-paths?limit_per_repeater=${limitPerRepeater}`
    ),
  getContactAnalytics: (params: { publicKey?: string; name?: string }, signal?: AbortSignal) => {
    const searchParams = new URLSearchParams();
    if (params.publicKey) searchParams.set('public_key', params.publicKey);
    if (params.name) searchParams.set('name', params.name);
    return fetchJson<ContactAnalytics>(`/contacts/analytics?${searchParams.toString()}`, {
      signal,
    });
  },
  deleteContact: (publicKey: string) =>
    fetchJson<{ status: string }>(`/contacts/${publicKey}`, {
      method: 'DELETE',
    }),
  bulkDeleteContacts: (publicKeys: string[]) =>
    fetchJson<{ deleted: number }>('/contacts/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ public_keys: publicKeys }),
    }),
  createContact: (publicKey: string, name?: string, tryHistorical?: boolean, type?: number) =>
    fetchJson<Contact>('/contacts', {
      method: 'POST',
      body: JSON.stringify({ public_key: publicKey, name, type, try_historical: tryHistorical }),
    }),
  markContactRead: (publicKey: string) =>
    fetchJson<{ status: string; public_key: string }>(`/contacts/${publicKey}/mark-read`, {
      method: 'POST',
    }),
  sendRepeaterCommand: (publicKey: string, command: string) =>
    fetchJson<CommandResponse>(`/contacts/${publicKey}/command`, {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),
  requestTrace: (publicKey: string) =>
    fetchJson<TraceResponse>(`/contacts/${publicKey}/trace`, {
      method: 'POST',
    }),
  requestPathDiscovery: (publicKey: string) =>
    fetchJson<PathDiscoveryResponse>(`/contacts/${publicKey}/path-discovery`, {
      method: 'POST',
    }),
  setContactRoutingOverride: (publicKey: string, route: string) =>
    fetchJson<{ status: string; public_key: string }>(`/contacts/${publicKey}/routing-override`, {
      method: 'POST',
      body: JSON.stringify({ route }),
    }),

  // Channels
  getChannels: () => fetchJson<Channel[]>('/channels'),
  createChannel: (name: string, key?: string) =>
    fetchJson<Channel>('/channels', {
      method: 'POST',
      body: JSON.stringify({ name, key }),
    }),
  bulkCreateHashtagChannels: (channelNames: string[], tryHistorical?: boolean) =>
    fetchJson<BulkCreateHashtagChannelsResult>('/channels/bulk-hashtag', {
      method: 'POST',
      body: JSON.stringify({ channel_names: channelNames, try_historical: tryHistorical }),
    }),
  deleteChannel: (key: string) =>
    fetchJson<{ status: string }>(`/channels/${key}`, { method: 'DELETE' }),
  getRejectedChannels: () => fetchJson<RejectedChannel[]>('/channels/rejected'),
  adoptChannel: (key: string) => fetchJson<Channel>(`/channels/${key}/adopt`, { method: 'POST' }),
  refuseChannel: (key: string) =>
    fetchJson<{ status: string; key: string }>(`/channels/${key}/refuse`, { method: 'POST' }),
  getChannelDetail: (key: string) => fetchJson<ChannelDetail>(`/channels/${key}/detail`),
  markChannelRead: (key: string) =>
    fetchJson<{ status: string; key: string }>(`/channels/${key}/mark-read`, {
      method: 'POST',
    }),
  setChannelFloodScopeOverride: (key: string, floodScopeOverride: string) =>
    fetchJson<Channel>(`/channels/${key}/flood-scope-override`, {
      method: 'POST',
      body: JSON.stringify({ flood_scope_override: floodScopeOverride }),
    }),

  setChannelPathHashModeOverride: (key: string, pathHashModeOverride: number | null) =>
    fetchJson<Channel>(`/channels/${key}/path-hash-mode-override`, {
      method: 'POST',
      body: JSON.stringify({ path_hash_mode_override: pathHashModeOverride }),
    }),

  // Messages
  getMessages: (
    params?: {
      limit?: number;
      offset?: number;
      type?: 'PRIV' | 'CHAN';
      conversation_key?: string;
      before?: number;
      before_id?: number;
      after?: number;
      after_id?: number;
      q?: string;
      outgoing?: boolean;
      since_id?: number;
    },
    signal?: AbortSignal
  ) => {
    const searchParams = new URLSearchParams();
    if (params?.limit !== undefined) searchParams.set('limit', params.limit.toString());
    if (params?.offset !== undefined) searchParams.set('offset', params.offset.toString());
    if (params?.type) searchParams.set('type', params.type);
    if (params?.conversation_key) searchParams.set('conversation_key', params.conversation_key);
    if (params?.before !== undefined) searchParams.set('before', params.before.toString());
    if (params?.before_id !== undefined) searchParams.set('before_id', params.before_id.toString());
    if (params?.after !== undefined) searchParams.set('after', params.after.toString());
    if (params?.after_id !== undefined) searchParams.set('after_id', params.after_id.toString());
    if (params?.q) searchParams.set('q', params.q);
    if (params?.outgoing !== undefined) searchParams.set('outgoing', String(params.outgoing));
    if (params?.since_id !== undefined) searchParams.set('since_id', params.since_id.toString());
    const query = searchParams.toString();
    return fetchJson<Message[]>(`/messages${query ? `?${query}` : ''}`, { signal });
  },
  getMessagesAround: (
    messageId: number,
    type?: 'PRIV' | 'CHAN',
    conversationKey?: string,
    signal?: AbortSignal
  ) => {
    const searchParams = new URLSearchParams();
    if (type) searchParams.set('type', type);
    if (conversationKey) searchParams.set('conversation_key', conversationKey);
    const query = searchParams.toString();
    return fetchJson<MessagesAroundResponse>(
      `/messages/around/${messageId}${query ? `?${query}` : ''}`,
      { signal }
    );
  },
  sendDirectMessage: (destination: string, text: string) =>
    fetchJson<Message>('/messages/direct', {
      method: 'POST',
      body: JSON.stringify({ destination, text }),
    }),
  sendChannelMessage: (channelKey: string, text: string) =>
    fetchJson<Message>('/messages/channel', {
      method: 'POST',
      body: JSON.stringify({ channel_key: channelKey, text }),
    }),
  resendChannelMessage: (messageId: number, newTimestamp?: boolean, clientAgeSec?: number) => {
    // clientAgeSec: browser-clock age, authoritative for the byte-perfect window
    // (utils/resendWindow.ts). Not sent with new_timestamp, which has no window.
    const params = new URLSearchParams();
    if (newTimestamp) params.set('new_timestamp', 'true');
    else if (clientAgeSec !== undefined && Number.isFinite(clientAgeSec)) {
      params.set('client_age_seconds', String(Math.max(0, Math.round(clientAgeSec * 1000) / 1000)));
    }
    const query = params.toString();
    return fetchJson<ResendChannelMessageResponse>(
      `/messages/channel/${messageId}/resend${query ? `?${query}` : ''}`,
      { method: 'POST' }
    );
  },
  deleteMessage: (messageId: number) =>
    fetchJson<{ status: string }>(`/messages/${messageId}`, { method: 'DELETE' }),

  // Packets
  getPacketsHistory: (query: RawPacketHistoryQuery = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    if (query.payload_type) params.set('payload_type', query.payload_type);
    if (query.since !== undefined) params.set('since', String(query.since));
    if (query.until !== undefined) params.set('until', String(query.until));
    if (query.limit !== undefined) params.set('limit', String(query.limit));
    if (query.after_id !== undefined) params.set('after_id', String(query.after_id));
    if (query.max_scan !== undefined) params.set('max_scan', String(query.max_scan));
    const qs = params.toString();
    return fetchJson<RawPacketHistoryResponse>(`/packets/history${qs ? `?${qs}` : ''}`, { signal });
  },
  getPacket: (packetId: number) => fetchJson<RawPacket>(`/packets/${packetId}`),
  getUndecryptedPacketCount: () => fetchJson<{ count: number }>('/packets/undecrypted/count'),
  getGroupTextSamples: (receivedSinceDays = 30) => {
    const params = new URLSearchParams({
      max_hashes: '80',
      max_per_hash: '4',
      max_scan: '8000',
      received_since_days: String(receivedSinceDays),
    });
    return fetchJson<GroupTextSamplesResponse>(
      `/packets/undecrypted/group-text-samples?${params.toString()}`
    );
  },
  decryptHistoricalPackets: (params: {
    key_type: 'channel' | 'contact';
    channel_key?: string;
    channel_name?: string;
  }) =>
    fetchJson<DecryptResult>('/packets/decrypt/historical', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
  runMaintenance: (options: { pruneUndecryptedDays?: number; purgeLinkedRawPackets?: boolean }) =>
    fetchJson<MaintenanceResult>('/packets/maintenance', {
      method: 'POST',
      body: JSON.stringify({
        ...(options.pruneUndecryptedDays !== undefined && {
          prune_undecrypted_days: options.pruneUndecryptedDays,
        }),
        ...(options.purgeLinkedRawPackets !== undefined && {
          purge_linked_raw_packets: options.purgeLinkedRawPackets,
        }),
      }),
    }),

  // Read State
  getUnreads: () => fetchJson<UnreadCounts>('/read-state/unreads'),
  markAllRead: () =>
    fetchJson<{ status: string; timestamp: number }>('/read-state/mark-all-read', {
      method: 'POST',
    }),

  // App Settings
  getSettings: () => fetchJson<AppSettings>('/settings'),
  getSettingsView: () => fetchJson<AppSettingsView>('/settings/view'),
  updateSettings: (settings: AppSettingsUpdate) =>
    fetchJson<AppSettings>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }),
  getTelemetryAlertCatalog: () =>
    fetchJson<TelemetryAlertCatalog>('/settings/telemetry-alert-catalog'),
  testNotificationDestination: (channel: NotificationDestinationChannel) =>
    fetchJson<{ status: string; channel?: NotificationDestinationChannel }>(
      '/settings/notification-destinations/test',
      {
        method: 'POST',
        body: JSON.stringify({ channel }),
      }
    ),

  getContactGroups: () => fetchJson<ContactGroup[]>('/contact-groups'),
  createContactGroup: (name: string) =>
    fetchJson<ContactGroup>('/contact-groups', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  updateContactGroup: (id: number, update: { name?: string; sort_order?: number }) =>
    fetchJson<ContactGroup>(`/contact-groups/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(update),
    }),
  deleteContactGroup: (id: number) =>
    fetchJson<{ status: string }>(`/contact-groups/${id}`, {
      method: 'DELETE',
    }),
  setContactGroupMembers: (id: number, publicKeys: string[]) =>
    fetchJson<ContactGroup>(`/contact-groups/${id}/members`, {
      method: 'PUT',
      body: JSON.stringify({ public_keys: publicKeys }),
    }),

  resolveDirectoryHops: (hops: string[]) =>
    fetchJson<DirectoryResolveHopsResponse>('/directory/resolve-hops', {
      method: 'POST',
      body: JSON.stringify({ hops }),
    }),
  getDirectoryMapNodes: (query?: DirectoryMapNodesQuery) => {
    const search = new URLSearchParams();
    if (query?.limit != null) search.set('limit', String(query.limit));
    if (query?.offset != null) search.set('offset', String(query.offset));
    if (query?.role) search.set('role', query.role);
    const qs = search.toString();
    return fetchJson<DirectoryMapNodesResponse>(`/directory/nodes${qs ? `?${qs}` : ''}`);
  },
  /** #live city plan: directory plus local GPS contacts. Observer GPS stays for geometry. */
  getLiveDirectoryMapNodes: () => fetchJson<DirectoryMapNodesResponse>('/directory/nodes/live'),
  searchDirectoryNodes: (q: string) =>
    fetchJson<DirectoryNodeSearchResponse>(`/directory/nodes/search?q=${encodeURIComponent(q)}`),
  getDirectoryNodeReach: (pubkey: string) =>
    fetchJson<DirectoryReachResponse>(`/directory/nodes/${encodeURIComponent(pubkey)}/reach`),
  getDirectoryNodeNeighbors: (pubkey: string) =>
    fetchJson<DirectoryNeighborsResponse>(
      `/directory/nodes/${encodeURIComponent(pubkey)}/neighbors`
    ),
  locate: (q: string, radiusKm?: number) => {
    const params = new URLSearchParams({ q });
    if (radiusKm != null) params.set('radius_km', String(radiusKm));
    return fetchJson<LocateResponse>(`/locate?${params.toString()}`);
  },
  resetDirectoryCache: () =>
    fetchJson<{ deleted: number }>('/directory/cache/reset', {
      method: 'POST',
    }),
  getPacketObserverReach: (packetHash: string) =>
    fetchJson<PacketObserverReachResponse>(
      `/directory/packets/${encodeURIComponent(packetHash)}/reach`
    ),
  runMeshTest: (floodScope: string) =>
    fetchJson<MeshTestResponse>('/tools/mesh-test', {
      method: 'POST',
      body: JSON.stringify({ flood_scope: floodScope }),
    }),
  getPacketObserverReachCounts: (hashes: string[]) =>
    fetchJson<PacketObserverReachCountsResponse>('/directory/packets/reach-counts', {
      method: 'POST',
      body: JSON.stringify({ hashes }),
    }),

  getJsonBackup: () => fetchJson<BackupExport>('/settings/backup/json'),
  /** The full backup (radio identity, settings, Wi-Fi, Meshloom), optionally encrypted. */
  exportFullBackup: async (
    passphrase?: string,
    includeAccounts = true
  ): Promise<{ data: unknown; filename: string; warnings: string[] }> => {
    const res = await fetch(`${API_BASE}/settings/backup/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...(passphrase ? { passphrase } : {}),
        include_accounts: includeAccounts,
      }),
    });
    if (!res.ok) {
      throw await apiErrorFrom(res);
    }
    const warnings = (res.headers.get('X-Backup-Warnings') ?? '')
      .split(',')
      .map((w) => w.trim())
      .filter(Boolean);
    return {
      data: await res.json(),
      filename: filenameFromDisposition(
        res.headers.get('Content-Disposition'),
        'meshloom-backup.json'
      ),
      warnings,
    };
  },
  inspectBackup: (body: { backup: unknown; passphrase?: string }) =>
    fetchJson<BackupInspectResult>('/settings/backup/inspect', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  startFullRestore: (body: BackupRestoreV2Request) =>
    fetchJson<{ job_id: string }>('/settings/backup/restore', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getRestoreJob: (jobId: string) =>
    fetchJson<BackupRestoreJob>(`/settings/backup/restore/jobs/${encodeURIComponent(jobId)}`),
  /** The most recent restore job, or null when there is none. */
  getLatestRestoreJob: async (): Promise<BackupRestoreJob | null> => {
    try {
      return await fetchJson<BackupRestoreJob>('/settings/backup/restore/jobs/latest');
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  },
  restoreJsonBackup: (payload: BackupExport) =>
    fetchJson<BackupRestoreResult>('/settings/backup/restore', {
      method: 'POST',
      body: JSON.stringify({ ...payload, confirm: true }),
    }),
  downloadDatabaseBackup: async () => {
    const res = await fetch(`${API_BASE}/settings/backup/database`);
    if (!res.ok) {
      const errorText = await res.text();
      throw new ApiError(errorText || res.statusText, res.status);
    }
    const blob = await res.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = 'meshcore.db';
    a.click();
    URL.revokeObjectURL(href);
  },

  // Block lists
  toggleBlockedKey: (key: string) =>
    fetchJson<AppSettings>('/settings/blocked-keys/toggle', {
      method: 'POST',
      body: JSON.stringify({ key }),
    }),
  toggleBlockedName: (name: string) =>
    fetchJson<AppSettings>('/settings/blocked-names/toggle', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  // Tracked telemetry
  toggleTrackedTelemetry: (publicKey: string) =>
    fetchJson<TrackedTelemetryResponse>('/settings/tracked-telemetry/toggle', {
      method: 'POST',
      body: JSON.stringify({ public_key: publicKey }),
    }),

  getTelemetrySchedule: () => fetchJson<TelemetrySchedule>('/settings/tracked-telemetry/schedule'),

  // Tracked contact telemetry
  toggleTrackedTelemetryContact: (publicKey: string) =>
    fetchJson<TrackedTelemetryContactsResponse>('/settings/tracked-telemetry-contacts/toggle', {
      method: 'POST',
      body: JSON.stringify({ public_key: publicKey }),
    }),

  getContactTelemetrySchedule: () =>
    fetchJson<TelemetrySchedule>('/settings/tracked-telemetry-contacts/schedule'),

  // Favorites
  toggleFavorite: (type: 'channel' | 'contact', id: string) =>
    fetchJson<{ type: string; id: string; favorite: boolean }>('/settings/favorites/toggle', {
      method: 'POST',
      body: JSON.stringify({ type, id }),
    }),

  togglePin: (type: 'channel' | 'contact', id: string) =>
    fetchJson<{ type: string; id: string; pinned: boolean }>('/settings/pins/toggle', {
      method: 'POST',
      body: JSON.stringify({ type, id }),
    }),

  toggleChannelMute: (key: string, durationSeconds?: number) =>
    fetchJson<{ key: string; muted: boolean; muted_until: number | null }>(
      '/settings/muted-channels/toggle',
      {
        method: 'POST',
        body: JSON.stringify({
          key,
          ...(durationSeconds === undefined ? {} : { duration_seconds: durationSeconds }),
        }),
      }
    ),

  // Fanout
  getFanoutConfigs: () => fetchJson<FanoutConfig[]>('/fanout'),
  createFanoutConfig: (config: {
    type: string;
    name: string;
    config: Record<string, unknown>;
    scope: Record<string, unknown>;
    enabled?: boolean;
  }) =>
    fetchJson<FanoutConfig>('/fanout', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  updateFanoutConfig: (
    id: string,
    update: {
      name?: string;
      config?: Record<string, unknown>;
      scope?: Record<string, unknown>;
      enabled?: boolean;
    }
  ) =>
    fetchJson<FanoutConfig>(`/fanout/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(update),
    }),
  deleteFanoutConfig: (id: string) =>
    fetchJson<{ deleted: boolean }>(`/fanout/${id}`, {
      method: 'DELETE',
    }),
  disableBotsUntilRestart: () =>
    fetchJson<{
      status: string;
      bots_disabled: boolean;
      bots_disabled_source: 'env' | 'until_restart';
    }>('/fanout/bots/disable-until-restart', {
      method: 'POST',
    }),

  // Statistics
  getStatistics: () => fetchJson<StatisticsResponse>('/statistics'),

  // Meshloom Stats community (browser talks only to this backend)
  getCommunity: () => fetchJson<CommunityStatus>('/community'),
  updateCommunity: (update: CommunityUpdate) =>
    fetchJson<CommunityStatus>('/community', {
      method: 'PATCH',
      body: JSON.stringify(update),
    }),
  retryCommunityOptOut: () =>
    fetchJson<CommunityStatus>('/community/opt-out/retry', {
      method: 'POST',
    }),
  getCommunityMeStats: () => fetchJson<CommunityMeStats>('/community/me/stats'),
  bindCommunityIata: (body: CommunityIataBindRequest) =>
    fetchJson<CommunityIataBindResult>('/community/me/iata', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  overrideCommunityIata: () =>
    fetchJson<CommunityIataBindResult>('/community/me/iata/override', {
      method: 'POST',
    }),
  getCommunityStats: () => fetchJson<CommunityPublicStats>('/community/stats'),
  getCommunityHashtags: () => fetchJson<CommunityHashtagsResponse>('/community/hashtags'),
  putCommunityHashtags: (names: string[]) =>
    fetchJson<CommunityHashtagsResponse>('/community/me/hashtags', {
      method: 'PUT',
      body: JSON.stringify({ names }),
    }),
  /** `iata`: regions this tab shows; null or omitted = every region. */
  subscribeCommunityLive: (sessionId?: string, iata?: string[] | null) =>
    fetchJson<CommunityLiveStatus>('/community/live/subscribe', {
      method: 'POST',
      body: JSON.stringify({
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(iata && iata.length ? { iata } : {}),
      }),
    }),
  getCommunityActiveIata: () => fetchJson<CommunityIataActiveResponse>('/community/iata/active'),
  unsubscribeCommunityLive: (sessionId: string) =>
    fetchJson<CommunityLiveStatus>(`/community/live/subscribe/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    }),
  restartCommunityLive: () =>
    fetchJson<CommunityLiveStatus>('/community/live/restart', {
      method: 'POST',
    }),

  /** Where an IATA code is (bundled airport list in the backend: nothing leaves). */
  searchCommunityAirports: async (code: string): Promise<CommunityAirportHit[]> => {
    const params = new URLSearchParams({ q: code });
    const payload = await fetchJson<{ airports: CommunityAirportHit[] }>(
      `/community/airports?${params.toString()}`
    );
    return payload.airports;
  },

  /**
   * Codes the IATA form proposes. Sends the radio's position, rounded to about
   * 1 km, to Community, even while it is off: ask only on the user's click.
   */
  suggestCommunityIata: () => fetchJson<CommunityIataSuggestion>('/community/iata/suggest'),

  // Granular repeater endpoints
  repeaterLogin: (publicKey: string, password: string) =>
    fetchJson<RepeaterLoginResponse>(`/contacts/${publicKey}/repeater/login`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  repeaterStatus: (publicKey: string) =>
    fetchJson<RepeaterStatusResponse>(`/contacts/${publicKey}/repeater/status`, {
      method: 'POST',
    }),
  /** Last known pane answers, read from the database. Never touches the radio. */
  repeaterPaneCache: (publicKey: string) =>
    fetchJson<Record<string, { data: unknown; fetched_at: number }>>(
      `/contacts/${publicKey}/repeater/cache`
    ),
  repeaterNeighbors: (publicKey: string) =>
    fetchJson<RepeaterNeighborsResponse>(`/contacts/${publicKey}/repeater/neighbors`, {
      method: 'POST',
    }),
  repeaterNodeInfo: (publicKey: string) =>
    fetchJson<RepeaterNodeInfoResponse>(`/contacts/${publicKey}/repeater/node-info`, {
      method: 'POST',
    }),
  repeaterAcl: (publicKey: string) =>
    fetchJson<RepeaterAclResponse>(`/contacts/${publicKey}/repeater/acl`, {
      method: 'POST',
    }),
  repeaterRadioSettings: (publicKey: string) =>
    fetchJson<RepeaterRadioSettingsResponse>(`/contacts/${publicKey}/repeater/radio-settings`, {
      method: 'POST',
    }),
  repeaterAdvertIntervals: (publicKey: string) =>
    fetchJson<RepeaterAdvertIntervalsResponse>(`/contacts/${publicKey}/repeater/advert-intervals`, {
      method: 'POST',
    }),
  repeaterOwnerInfo: (publicKey: string) =>
    fetchJson<RepeaterOwnerInfoResponse>(`/contacts/${publicKey}/repeater/owner-info`, {
      method: 'POST',
    }),
  repeaterRegions: (publicKey: string) =>
    fetchJson<RepeaterRegionsResponse>(`/contacts/${publicKey}/repeater/regions`, {
      method: 'POST',
    }),
  repeaterLppTelemetry: (publicKey: string) =>
    fetchJson<RepeaterLppTelemetryResponse>(`/contacts/${publicKey}/repeater/lpp-telemetry`, {
      method: 'POST',
    }),
  repeaterTelemetryHistory: (publicKey: string) =>
    fetchJson<TelemetryHistoryEntry[]>(`/contacts/${publicKey}/repeater/telemetry-history`),
  // Contact telemetry (universal, any contact type)
  requestContactTelemetry: (publicKey: string) =>
    fetchJson<ContactTelemetryResponse>(`/contacts/${publicKey}/telemetry`, {
      method: 'POST',
    }),
  contactTelemetryHistory: (publicKey: string) =>
    fetchJson<TelemetryHistoryEntry[]>(`/contacts/${publicKey}/telemetry-history`),
  roomLogin: (publicKey: string, password: string) =>
    fetchJson<RepeaterLoginResponse>(`/contacts/${publicKey}/room/login`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  roomStatus: (publicKey: string) =>
    fetchJson<RepeaterStatusResponse>(`/contacts/${publicKey}/room/status`, {
      method: 'POST',
    }),
  roomAcl: (publicKey: string) =>
    fetchJson<RepeaterAclResponse>(`/contacts/${publicKey}/room/acl`, {
      method: 'POST',
    }),
  roomLppTelemetry: (publicKey: string) =>
    fetchJson<RepeaterLppTelemetryResponse>(`/contacts/${publicKey}/room/lpp-telemetry`, {
      method: 'POST',
    }),

  // Push Notifications
  getVapidPublicKey: () => fetchJson<{ public_key: string }>('/push/vapid-public-key'),
  pushSubscribe: (subscription: {
    endpoint: string;
    p256dh: string;
    auth: string;
    label?: string;
    language?: 'fr' | 'en';
  }) =>
    fetchJson<PushSubscriptionInfo>('/push/subscribe', {
      method: 'POST',
      body: JSON.stringify(subscription),
    }),
  getPushSubscriptions: () => fetchJson<PushSubscriptionInfo[]>('/push/subscriptions'),
  deletePushSubscription: (id: string) =>
    fetchJson<{ deleted: boolean }>(`/push/subscriptions/${id}`, { method: 'DELETE' }),
  testPushSubscription: (id: string) =>
    fetchJson<{ status: string }>(`/push/subscriptions/${id}/test`, { method: 'POST' }),
  getPlugins: () => fetchJson<PluginInstance[]>('/plugins'),
  getBundledPlugins: () => fetchJson<BundledPlugin[]>('/plugins/bundled'),
  installPlugin: (body: PluginInstallRequest) =>
    fetchJson<PluginInstance>('/plugins/install', { method: 'POST', body: JSON.stringify(body) }),
  patchPlugin: (id: string, body: PluginPatch) =>
    fetchJson<PluginInstance>(`/plugins/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  createLocalPlugin: (body: LocalPluginCreate) =>
    fetchJson<PluginInstance>('/plugins', { method: 'POST', body: JSON.stringify(body) }),
  putPluginSource: (id: string, source: string) =>
    fetchJson<PluginSourceResult>(`/plugins/${encodeURIComponent(id)}/source`, {
      method: 'PUT',
      body: JSON.stringify({ source }),
    }),
  checkPlugin: (id: string) =>
    fetchJson<PluginCheckResult>(`/plugins/${encodeURIComponent(id)}/check`, { method: 'POST' }),
  testPlugin: (id: string, body: PluginTestRequest) =>
    fetchJson<PluginTestResult>(`/plugins/${encodeURIComponent(id)}/test`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  restartPlugin: (id: string) =>
    fetchJson<PluginInstance>(`/plugins/${encodeURIComponent(id)}/restart`, { method: 'POST' }),
  getPluginLogs: (id: string, limit = 200) =>
    fetchJson<PluginLogs>(`/plugins/${encodeURIComponent(id)}/logs?limit=${limit}`, {
      cache: 'no-store',
    }),
  getPluginDiagnostic: (id: string) =>
    fetchJson<PluginDiagnostic>(`/plugins/${encodeURIComponent(id)}/diagnostic`, {
      cache: 'no-store',
    }),
  deletePlugin: (id: string, keepData: boolean) =>
    fetchJson<void>(`/plugins/${encodeURIComponent(id)}?keep_data=${keepData}`, {
      method: 'DELETE',
    }),
  getShareStatus: () => fetchJson<ShareStatus>('/share/status'),
  shortenLink: (url: string) =>
    fetchJson<ShareLink>('/share/links', { method: 'POST', body: JSON.stringify({ url }) }),
  getShareMeta: (id: string) => fetchJson<ShareMeta>(`/share/${encodeURIComponent(id)}/meta`),
  // Notifications (admin). The push preference routes below are deprecated shims, removed
  // in 5.2.0: use the rules.
  getNotificationTypes: () => fetchJson<NotificationTypeInfo[]>('/notifications/types'),
  getNotificationEvents: () =>
    fetchJson<{ contract_version: number; events: NotificationEventInfo[] }>(
      '/notifications/events'
    ),
  getNotificationChannels: () => fetchJson<NotificationChannel[]>('/notifications/channels'),
  getNotificationChannel: (id: string) =>
    fetchJson<NotificationChannel>(`/notifications/channels/${encodeURIComponent(id)}`),
  createNotificationChannel: (body: NotificationChannelCreate) =>
    fetchJson<NotificationChannel>('/notifications/channels', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateNotificationChannel: (id: string, body: NotificationChannelPatch) =>
    fetchJson<NotificationChannel>(`/notifications/channels/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteNotificationChannel: (id: string) =>
    fetchJson<{ deleted: boolean }>(`/notifications/channels/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  /** Test a configuration before saving it: nothing is persisted. */
  testNotificationConfig: (body: NotificationChannelCreate) =>
    fetchJson<{ status: string }>('/notifications/channels/test', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  /** One test per 5 s per channel: a second one answers 429 `rate_limited`. */
  testNotificationChannel: (id: string) =>
    fetchJson<{ status: string }>(`/notifications/channels/${encodeURIComponent(id)}/test`, {
      method: 'POST',
    }),
  getNotificationRules: () => fetchJson<NotificationRules>('/notifications/rules'),
  patchNotificationRules: (body: NotificationRulesPatch) =>
    fetchJson<NotificationRules>('/notifications/rules', {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  /** `channels: null` clears the exception of the conversation. */
  setNotificationConversationRule: (key: string, channels: Record<string, boolean> | null) =>
    fetchJson<NotificationRules>(`/notifications/rules/conversations/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({ channels }),
    }),
  getNotificationDeliveries: (params: { channel?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.channel) query.set('channel', params.channel);
    if (params.limit) query.set('limit', String(params.limit));
    const suffix = query.toString();
    return fetchJson<NotificationDeliveries>(
      `/notifications/deliveries${suffix ? `?${suffix}` : ''}`
    );
  },
  getPushPreferences: () => fetchJson<PushPreferences>('/push/preferences'),
  patchPushPreferences: (partial: { defaults?: PushDefaultsPatch; vapid_subject?: string }) =>
    fetchJson<PushPreferences>('/push/preferences', {
      method: 'PATCH',
      body: JSON.stringify(partial),
    }),
  setPushConversationOverride: (
    key: string,
    override: boolean | ConversationMediaOverride | null
  ) =>
    fetchJson<PushPreferences>(`/push/preferences/conversations/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({ override }),
    }),
};
