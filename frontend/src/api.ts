import type { TFunction } from 'i18next';

import type {
  AppSettings,
  AppSettingsUpdate,
  BackupExport,
  BackupRestoreResult,
  ContactGroup,
  BulkCreateHashtagChannelsResult,
  Channel,
  ChannelDetail,
  CommandResponse,
  Contact,
  ContactAnalytics,
  ContactAdvertPathSummary,
  DirectoryMapNodesQuery,
  DirectoryMapNodesResponse,
  DirectoryNodeRole,
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
  MaintenanceResult,
  Message,
  MessagesAroundResponse,
  RawPacket,
  RadioAdvertMode,
  RadioBleDeviceInfo,
  RadioConfig,
  RadioConfigUpdate,
  RadioIdentityActionResponse,
  RadioProxyStatus,
  RadioProxyUpdate,
  RadioTransportConfig,
  RadioTransportUpdate,
  RadioDiscoveryResponse,
  RadioRegionDiscoveryResponse,
  RadioTraceHopRequest,
  RadioTraceResponse,
  RadioDiscoveryTarget,
  PathDiscoveryResponse,
  PushDefaults,
  PushPreferences,
  PushSubscriptionInfo,
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
  CommunityLiveStatus,
  CommunityMeStats,
  CommunityPublicStats,
  CommunityAirportHit,
  CommunityStatus,
  CommunityUpdate,
  TraceResponse,
  UnreadCounts,
} from './types';

const API_BASE = './api';

/** Error thrown by API calls, carrying the HTTP status so callers can tell
 * retryable failures from ones the mesh already answered (e.g. 422 timeouts). */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: unknown = undefined
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
      const translated = t(key, parsed.params);
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
  const res = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      ...(hasBody && { 'Content-Type': 'application/json' }),
      ...options?.headers,
    },
  });
  if (!res.ok) {
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
    throw new ApiError(errorMessage, res.status, detail);
  }
  return res.json();
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
  // Health
  getHealth: () => fetchJson<HealthStatus>('/health'),

  // Radio config
  getRadioConfig: () => fetchJson<RadioConfig>('/radio/config'),
  updateRadioConfig: (config: RadioConfigUpdate) =>
    fetchJson<RadioConfig>('/radio/config', {
      method: 'PATCH',
      body: JSON.stringify(config),
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
    fetchJson<{ status: string; message: string; connected: boolean }>('/radio/reconnect', {
      method: 'POST',
    }),
  getRadioTransport: () => fetchJson<RadioTransportConfig>('/radio/transport'),
  updateRadioTransport: (body: RadioTransportUpdate) =>
    fetchJson<RadioTransportConfig>('/radio/transport', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
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
  resendChannelMessage: (messageId: number, newTimestamp?: boolean) =>
    fetchJson<ResendChannelMessageResponse>(
      `/messages/channel/${messageId}/resend${newTimestamp ? '?new_timestamp=true' : ''}`,
      { method: 'POST' }
    ),
  deleteMessage: (messageId: number) =>
    fetchJson<{ status: string }>(`/messages/${messageId}`, { method: 'DELETE' }),

  // Packets
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
  updateSettings: (settings: AppSettingsUpdate) =>
    fetchJson<AppSettings>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }),

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
  getDirectoryMapNodesAll: async (
    pageSize = 500,
    role?: DirectoryNodeRole
  ): Promise<DirectoryMapNodesResponse> => {
    const first = await api.getDirectoryMapNodes({ limit: pageSize, offset: 0, role });
    const total = first.total;
    if (total == null || first.nodes.length >= total || first.nodes.length === 0) {
      return first;
    }
    const nodes = [...first.nodes];
    while (nodes.length < total) {
      const page = await api.getDirectoryMapNodes({ limit: pageSize, offset: nodes.length, role });
      if (page.nodes.length === 0) break;
      nodes.push(...page.nodes);
      if (page.nodes.length < pageSize) break;
    }
    return { nodes, total };
  },
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
  getPacketObserverReachCounts: (hashes: string[]) =>
    fetchJson<PacketObserverReachCountsResponse>('/directory/packets/reach-counts', {
      method: 'POST',
      body: JSON.stringify({ hashes }),
    }),

  getJsonBackup: () => fetchJson<BackupExport>('/settings/backup/json'),
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

  toggleChannelMute: (key: string) =>
    fetchJson<{ key: string; muted: boolean }>('/settings/muted-channels/toggle', {
      method: 'POST',
      body: JSON.stringify({ key }),
    }),

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
  getCommunityHashtags: (iata: string) =>
    fetchJson<CommunityHashtagsResponse>(`/community/iata/${encodeURIComponent(iata)}/hashtags`),
  putCommunityHashtags: (names: string[]) =>
    fetchJson<CommunityHashtagsResponse>('/community/me/hashtags', {
      method: 'PUT',
      body: JSON.stringify({ names }),
    }),
  subscribeCommunityLive: (sessionId?: string) =>
    fetchJson<CommunityLiveStatus>('/community/live/subscribe', {
      method: 'POST',
      body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
    }),
  unsubscribeCommunityLive: (sessionId: string) =>
    fetchJson<CommunityLiveStatus>(`/community/live/subscribe/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    }),
  relancerCommunityLive: () =>
    fetchJson<CommunityLiveStatus>('/community/live/relancer', {
      method: 'POST',
    }),

  searchCommunityAirports: async (
    query: string,
    locale?: string
  ): Promise<CommunityAirportHit[]> => {
    const params = new URLSearchParams({ q: query });
    if (locale) params.set('locale', locale);
    const payload = await fetchJson<{ airports: CommunityAirportHit[] }>(
      `/community/airports?${params.toString()}`
    );
    return payload.airports;
  },

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
  getPushPreferences: () => fetchJson<PushPreferences>('/push/preferences'),
  patchPushPreferences: (partial: { defaults?: Partial<PushDefaults>; vapid_subject?: string }) =>
    fetchJson<PushPreferences>('/push/preferences', {
      method: 'PATCH',
      body: JSON.stringify(partial),
    }),
  setPushConversationOverride: (key: string, override: boolean | null) =>
    fetchJson<PushPreferences>(`/push/preferences/conversations/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({ override }),
    }),
};
