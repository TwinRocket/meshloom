import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Ban, ChevronDown, ChevronRight, Crosshair, Search, Star } from 'lucide-react';
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { api, isAbortError } from '../api';
import { formatTime } from '../utils/messageParser';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';
import {
  getContactDisplayName,
  isPrefixOnlyContact,
  isUnknownFullKeyContact,
} from '../utils/pubkey';
import {
  isValidLocation,
  calculateDistance,
  formatDistance,
  getDirectContactRoute,
  getEffectiveContactRoute,
  hasRoutingOverride,
  parsePathHops,
} from '../utils/pathUtils';
import { isPublicChannelKey } from '../utils/publicChannel';
import { getMapFocusHash } from '../utils/urlHash';
import { handleKeyboardActivate } from '../utils/a11y';
import { ContactAvatar } from './ContactAvatar';
import { MeshcoreShareQr } from './MeshcoreShareQr';
import { LppSensorRow, formatLppLabel } from './repeater/repeaterPaneShared';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { toast } from './ui/sonner';
import { formatContactAddUri, type MeshcoreContactType } from '../utils/meshcoreUri';
import { useDistanceUnit } from '../contexts/DistanceUnitContext';
import { useEntranceSettled } from '../hooks/useEntranceSettled';
import { CONTACT_TYPE_REPEATER } from '../types';
import type {
  Contact,
  ContactActiveRoom,
  ContactAnalytics,
  ContactAnalyticsHourlyBucket,
  ContactAnalyticsWeeklyBucket,
  LppSensor,
  RadioConfig,
  TelemetryHistoryEntry,
  TelemetryLppSensor,
} from '../types';
import { formatNumber } from '../utils/formatNumber';

function contactTypeLabel(type: number, t: (key: string) => string): string {
  if (type === 1) return t('contactInfo.typeClient');
  if (type === 2) return t('contactInfo.typeRepeater');
  if (type === 3) return t('contactInfo.typeRoom');
  if (type === 4) return t('contactInfo.typeSensor');
  return t('contactInfo.typeUnknown');
}

function formatPathHashMode(
  mode: number,
  t: (key: string, opts?: { n: number }) => string
): string | null {
  if (mode < 0 || mode > 2) {
    return null;
  }
  return t('contactInfo.byteIds', { n: mode + 1 });
}

function formatContactRouteLabel(
  pathLen: number,
  t: (key: string, opts?: { count: number }) => string,
  capitalize = false
): string {
  const label =
    pathLen === -1
      ? t('contactInfo.flood')
      : pathLen === 0
        ? t('contactInfo.hopDirect')
        : t('contactInfo.hopCount', { count: pathLen });
  return capitalize ? label.charAt(0).toUpperCase() + label.slice(1) : label;
}

interface ContactInfoPaneProps {
  contactKey: string | null;
  fromChannel?: boolean;
  onClose: () => void;
  contacts: Contact[];
  config: RadioConfig | null;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onNavigateToChannel?: (channelKey: string) => void;
  onSearchMessagesByKey?: (publicKey: string) => void;
  onSearchMessagesByName?: (name: string) => void;
  onLocateContact?: (publicKey: string) => void;
  blockedKeys?: string[];
  blockedNames?: string[];
  onToggleBlockedKey?: (key: string) => void;
  onToggleBlockedName?: (name: string) => void;
  trackedTelemetryContacts?: string[];
  onToggleTrackedTelemetryContact?: (publicKey: string) => Promise<void>;
}

export function ContactInfoPane({
  contactKey,
  fromChannel = false,
  onClose,
  contacts,
  config,
  onToggleFavorite,
  onNavigateToChannel,
  onSearchMessagesByKey,
  onSearchMessagesByName,
  onLocateContact,
  blockedKeys = [],
  blockedNames = [],
  onToggleBlockedKey,
  onToggleBlockedName,
  trackedTelemetryContacts = [],
  onToggleTrackedTelemetryContact,
}: ContactInfoPaneProps) {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();
  const isNameOnly = contactKey?.startsWith('name:') ?? false;
  const nameOnlyValue = isNameOnly && contactKey ? contactKey.slice(5) : null;

  const [analytics, setAnalytics] = useState<ContactAnalytics | null>(null);
  const [loading, setLoading] = useState(false);
  const [telemetryLoading, setTelemetryLoading] = useState(false);
  const [telemetryHistory, setTelemetryHistory] = useState<TelemetryHistoryEntry[]>([]);

  // Get live contact data from contacts array (real-time via WS)
  const liveContact =
    contactKey && !isNameOnly ? (contacts.find((c) => c.public_key === contactKey) ?? null) : null;

  // Defer mounting Recharts containers until the pane's slide-in animation
  // settles; mounting them mid-transform crashes Safari (React #185). See #317.
  const chartsReady = useEntranceSettled(contactKey !== null);

  useEffect(() => {
    if (!contactKey) {
      setAnalytics(null);
      return;
    }

    const controller = new AbortController();
    setAnalytics(null);
    setLoading(true);
    const request =
      isNameOnly && nameOnlyValue
        ? api.getContactAnalytics({ name: nameOnlyValue }, controller.signal)
        : api.getContactAnalytics({ publicKey: contactKey }, controller.signal);

    request
      .then((data) => {
        if (!controller.signal.aborted) setAnalytics(data);
      })
      .catch((err) => {
        if (!isAbortError(err)) {
          console.error('Failed to fetch contact analytics:', err);
          toast.error(t('contactInfo.loadFailed'));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [contactKey, isNameOnly, nameOnlyValue]);

  // Load telemetry history when pane opens for a contact
  useEffect(() => {
    if (!contactKey || isNameOnly) {
      setTelemetryHistory([]);
      return;
    }
    let cancelled = false;
    api
      .contactTelemetryHistory(contactKey)
      .then((data) => {
        if (!cancelled) setTelemetryHistory(data);
      })
      .catch(() => {
        if (!cancelled) setTelemetryHistory([]);
      });
    return () => {
      cancelled = true;
    };
  }, [contactKey, isNameOnly]);

  const handleFetchTelemetry = useCallback(async () => {
    if (!contactKey || isNameOnly) return;
    setTelemetryLoading(true);
    try {
      const result = await api.requestContactTelemetry(contactKey);
      setTelemetryHistory(result.telemetry_history);
    } catch (err) {
      if (!isAbortError(err)) {
        toast.error(err instanceof Error ? err.message : t('contactInfo.telemetryFailed'));
      }
    } finally {
      setTelemetryLoading(false);
    }
  }, [contactKey, isNameOnly]);

  // Use live contact data where available, fall back to analytics snapshot
  const contact = liveContact ?? analytics?.contact ?? null;

  const distFromUs =
    contact &&
    config &&
    isValidLocation(config.lat, config.lon) &&
    isValidLocation(contact.lat, contact.lon)
      ? calculateDistance(config.lat, config.lon, contact.lat, contact.lon)
      : null;
  const effectiveRoute = contact ? getEffectiveContactRoute(contact) : null;
  const directRoute = contact ? getDirectContactRoute(contact) : null;
  const pathHashModeLabel =
    effectiveRoute && effectiveRoute.pathLen >= 0
      ? formatPathHashMode(effectiveRoute.pathHashMode, t)
      : null;
  const learnedRouteLabel = directRoute
    ? formatContactRouteLabel(directRoute.path_len, t, true)
    : null;
  const isPrefixOnlyResolvedContact = contact ? isPrefixOnlyContact(contact.public_key) : false;
  const isUnknownFullKeyResolvedContact =
    contact !== null &&
    !isPrefixOnlyResolvedContact &&
    isUnknownFullKeyContact(contact.public_key, contact.last_advert);
  const isRepeater = contact?.type === CONTACT_TYPE_REPEATER;
  const shareUri = contact
    ? formatContactAddUri({
        name: contact.name ?? '',
        publicKey: contact.public_key,
        type: contact.type as MeshcoreContactType,
      })
    : null;

  return (
    <Sheet open={contactKey !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-[400px] p-0 flex flex-col">
        <SheetHeader className="sr-only">
          <SheetTitle>{t('contactInfo.title')}</SheetTitle>
          <SheetDescription>{t('contactInfo.description')}</SheetDescription>
        </SheetHeader>

        {isNameOnly && nameOnlyValue ? (
          <div className="flex-1 overflow-y-auto">
            {/* Name-only header */}
            <div className="px-5 pt-5 pb-4 border-b border-border">
              <div className="flex items-start gap-4">
                <ContactAvatar
                  name={analytics?.name ?? nameOnlyValue}
                  publicKey={`name:${nameOnlyValue}`}
                  size={56}
                />
                <div className="flex-1 min-w-0">
                  <h2 className="text-lg font-semibold truncate">
                    {analytics?.name ?? nameOnlyValue}
                  </h2>
                  <p className="text-xs text-muted-foreground mt-1">
                    {t('contactInfo.nameUnknownKey')}
                  </p>
                </div>
              </div>
            </div>

            {/* Block by name toggle */}
            {onToggleBlockedName && (
              <div className="px-5 py-3 border-b border-border">
                <button
                  type="button"
                  className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                  onClick={() => onToggleBlockedName(nameOnlyValue)}
                >
                  {blockedNames.includes(nameOnlyValue) ? (
                    <>
                      <Ban className="h-4.5 w-4.5 text-destructive" aria-hidden="true" />
                      <span>{t('contactInfo.unblockName')}</span>
                    </>
                  ) : (
                    <>
                      <Ban className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                      <span>{t('contactInfo.blockName')}</span>
                    </>
                  )}
                </button>
              </div>
            )}

            {onSearchMessagesByName && (
              <div className="px-5 py-3 border-b border-border">
                <button
                  type="button"
                  className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                  onClick={() => onSearchMessagesByName(nameOnlyValue)}
                >
                  <Search className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                  <span>{t('contactInfo.searchByName')}</span>
                </button>
              </div>
            )}

            {fromChannel && (
              <ChannelAttributionWarning
                nameOnly
                includeAliasNote={false}
                className="border-b border-border mx-0 my-0 rounded-none px-5 py-3"
              />
            )}

            <MessageStatsSection
              dmMessageCount={0}
              channelMessageCount={analytics?.channel_message_count ?? 0}
              showDirectMessages={false}
            />

            {analytics?.name_first_seen_at && (
              <div className="px-5 py-3 border-b border-border">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <InfoItem
                    label={t('contactInfo.nameFirstInUse')}
                    value={formatTime(analytics.name_first_seen_at)}
                  />
                </div>
              </div>
            )}

            <ActivityChartsSection analytics={analytics} ready={chartsReady} />

            <MostActiveChannelsSection
              channels={analytics?.most_active_rooms ?? []}
              onNavigateToChannel={onNavigateToChannel}
            />
          </div>
        ) : loading && !analytics && !contact ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            {t('contactInfo.loading')}
          </div>
        ) : contact ? (
          <div className="flex-1 overflow-y-auto">
            {/* Header */}
            <div className="px-5 pt-5 pb-4 border-b border-border">
              <div className="flex items-start gap-4">
                <ContactAvatar
                  name={contact.name}
                  publicKey={contact.public_key}
                  size={56}
                  contactType={contact.type}
                />
                <div className="flex-1 min-w-0">
                  <h2 className="text-lg font-semibold truncate">
                    {getContactDisplayName(contact.name, contact.public_key, contact.last_advert)}
                  </h2>
                  <span
                    className="text-xs font-mono text-muted-foreground cursor-pointer hover:text-primary transition-colors block truncate"
                    role="button"
                    tabIndex={0}
                    onKeyDown={handleKeyboardActivate}
                    onClick={() => {
                      navigator.clipboard.writeText(contact.public_key);
                      toast.success(t('contactInfo.publicKeyCopied'));
                    }}
                    title={t('contactInfo.clickToCopy')}
                  >
                    {contact.public_key}
                  </span>
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                      {contactTypeLabel(contact.type, t)}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {isPrefixOnlyResolvedContact && (
              <div className="mx-5 mt-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {t('contactInfo.prefixOnly')}
              </div>
            )}

            {isUnknownFullKeyResolvedContact && (
              <div className="mx-5 mt-4 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
                {t('contactInfo.unknownFullKey')}
              </div>
            )}

            {/* Info grid */}
            <div className="px-5 py-3 border-b border-border">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                {contact.last_seen && (
                  <InfoItem
                    label={t('contactInfo.lastSeen')}
                    value={formatTime(contact.last_seen)}
                  />
                )}
                {contact.first_seen && (
                  <InfoItem
                    label={t('contactInfo.firstHeard')}
                    value={formatTime(contact.first_seen)}
                  />
                )}
                {contact.last_contacted && (
                  <InfoItem
                    label={t('contactInfo.lastContacted')}
                    value={formatTime(contact.last_contacted)}
                  />
                )}
                {distFromUs !== null && (
                  <InfoItem
                    label={t('contactInfo.distance')}
                    value={formatDistance(distFromUs, distanceUnit)}
                  />
                )}
                {effectiveRoute && (
                  <InfoItem
                    label={t('contactInfo.routing')}
                    value={
                      effectiveRoute.forced ? (
                        <span>
                          {formatContactRouteLabel(effectiveRoute.pathLen, t, true)}{' '}
                          <span className="text-destructive">{t('contactInfo.forced')}</span>
                        </span>
                      ) : (
                        formatContactRouteLabel(effectiveRoute.pathLen, t, true)
                      )
                    }
                  />
                )}
                {hasRoutingOverride(contact) && learnedRouteLabel && (
                  <InfoItem label={t('contactInfo.learnedRoute')} value={learnedRouteLabel} />
                )}
                {pathHashModeLabel && (
                  <InfoItem label={t('contactInfo.hopWidth')} value={pathHashModeLabel} />
                )}
              </div>
            </div>

            {onLocateContact && !isNameOnly && contact && (
              <div className="px-5 py-3 border-b border-border">
                <button
                  type="button"
                  className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                  onClick={() => onLocateContact(contact.public_key)}
                  data-testid="locate-contact-cta"
                >
                  <Crosshair className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                  <span>{t('locate.estimateZone')}</span>
                </button>
              </div>
            )}

            {/* GPS */}
            {isValidLocation(contact.lat, contact.lon) && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>{t('contactInfo.location')}</SectionLabel>
                <span
                  className="text-sm font-mono cursor-pointer hover:text-primary hover:underline transition-colors"
                  role="button"
                  tabIndex={0}
                  onKeyDown={handleKeyboardActivate}
                  onClick={() => {
                    const url =
                      window.location.origin +
                      window.location.pathname +
                      getMapFocusHash(contact.public_key);
                    window.open(url, '_blank');
                  }}
                  title={t('contactInfo.viewOnMap')}
                >
                  {contact.lat!.toFixed(5)}, {contact.lon!.toFixed(5)}
                </span>
              </div>
            )}

            {/* Contact Telemetry */}
            <ContactTelemetrySection
              contact={contact}
              loading={telemetryLoading}
              onFetch={handleFetchTelemetry}
              telemetryHistory={telemetryHistory}
              isTracked={trackedTelemetryContacts.includes(contact.public_key)}
              onToggleTracked={onToggleTrackedTelemetryContact}
            />

            {/* Favorite toggle */}
            <div className="px-5 py-3 border-b border-border">
              <button
                type="button"
                className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                onClick={() => onToggleFavorite('contact', contact.public_key)}
                title={t('contactInfo.favoriteHelp')}
              >
                {contact.favorite ? (
                  <>
                    <Star className="h-4.5 w-4.5 fill-current text-favorite" aria-hidden="true" />
                    <span>{t('contactInfo.removeFavorite')}</span>
                  </>
                ) : (
                  <>
                    <Star className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                    <span>{t('contactInfo.addFavorite')}</span>
                  </>
                )}
              </button>
            </div>

            {shareUri && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>{t('share.title')}</SectionLabel>
                <MeshcoreShareQr uri={shareUri} />
              </div>
            )}

            {/* Block toggles */}
            {(onToggleBlockedKey || onToggleBlockedName) && (
              <div className="px-5 py-3 border-b border-border space-y-2">
                {onToggleBlockedKey && (
                  <button
                    type="button"
                    className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                    onClick={() => onToggleBlockedKey(contact.public_key)}
                  >
                    {blockedKeys.includes(contact.public_key.toLowerCase()) ? (
                      <>
                        <Ban className="h-4.5 w-4.5 text-destructive" aria-hidden="true" />
                        <span>{t('contactInfo.unblockKey')}</span>
                      </>
                    ) : (
                      <>
                        <Ban className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                        <span>{t('contactInfo.blockKey')}</span>
                      </>
                    )}
                  </button>
                )}
                {onToggleBlockedName && contact.name && (
                  <button
                    type="button"
                    className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                    onClick={() => onToggleBlockedName(contact.name!)}
                  >
                    {blockedNames.includes(contact.name) ? (
                      <>
                        <Ban className="h-4.5 w-4.5 text-destructive" aria-hidden="true" />
                        <span>{t('contactInfo.unblockNameQuoted', { name: contact.name })}</span>
                      </>
                    ) : (
                      <>
                        <Ban className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                        <span>{t('contactInfo.blockNameQuoted', { name: contact.name })}</span>
                      </>
                    )}
                  </button>
                )}
              </div>
            )}

            {!isRepeater && onSearchMessagesByKey && (
              <div className="px-5 py-3 border-b border-border">
                <button
                  type="button"
                  className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                  onClick={() => onSearchMessagesByKey(contact.public_key)}
                >
                  <Search className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                  <span>{t('contactInfo.searchByKey')}</span>
                </button>
              </div>
            )}

            {/* Nearest Repeaters (Hops) — last 7 days only */}
            {analytics &&
              (() => {
                const sevenDaysAgo = Math.floor(Date.now() / 1000) - 7 * 86400;
                const recent = analytics.nearest_repeaters.filter(
                  (r) => r.last_seen >= sevenDaysAgo
                );
                if (recent.length === 0) return null;
                return (
                  <div className="px-5 py-3 border-b border-border">
                    <SectionLabel>{t('contactInfo.nearestHops')}</SectionLabel>
                    <div className="space-y-1">
                      {recent.map((r) => (
                        <div
                          key={r.public_key}
                          className="flex justify-between items-center text-sm"
                        >
                          <span className="truncate">{r.name || r.public_key.slice(0, 12)}</span>
                          <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
                            {r.path_len === 0
                              ? t('contactInfo.hopDirect')
                              : t('contactInfo.hopCount', { count: r.path_len })}{' '}
                            · {t('contactInfo.heardCount', { count: r.heard_count })}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}

            {/* Geographically nearest repeaters (repeaters only) */}
            {isRepeater && contact && isValidLocation(contact.lat, contact.lon) && (
              <NearbyRepeatersSection
                contact={contact}
                contacts={contacts}
                distanceUnit={distanceUnit}
              />
            )}

            {/* Advert Paths */}
            {analytics && analytics.advert_paths.length > 0 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>{t('contactInfo.advertPaths')}</SectionLabel>
                <div className="space-y-1.5">
                  {analytics.advert_paths.map((p) => (
                    <div
                      key={p.path + p.first_seen}
                      className="flex justify-between items-start gap-2 text-sm"
                    >
                      <span className="font-mono text-xs break-all">
                        {p.path
                          ? parsePathHops(p.path, p.path_len).join(' → ')
                          : t('contactInfo.directPath')}
                      </span>
                      <span className="text-xs text-muted-foreground flex-shrink-0">
                        {p.heard_count}x · {formatTime(p.last_seen)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {fromChannel && (
              <ChannelAttributionWarning
                includeAliasNote={Boolean(analytics && analytics.name_history.length > 1)}
              />
            )}

            {/* AKA (Name History) - only show if more than one name */}
            {analytics && analytics.name_history.length > 1 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>{t('contactInfo.aka')}</SectionLabel>
                <div className="space-y-1">
                  {analytics.name_history.map((h) => (
                    <div key={h.name} className="flex justify-between items-center text-sm">
                      <span className="font-medium truncate">{h.name}</span>
                      <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
                        {formatTime(h.first_seen)} &ndash; {formatTime(h.last_seen)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {!isRepeater && (
              <>
                <MessageStatsSection
                  dmMessageCount={analytics?.dm_message_count ?? 0}
                  channelMessageCount={analytics?.channel_message_count ?? 0}
                />

                <ActivityChartsSection analytics={analytics} ready={chartsReady} />

                <MostActiveChannelsSection
                  channels={analytics?.most_active_rooms ?? []}
                  onNavigateToChannel={onNavigateToChannel}
                />
              </>
            )}
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            {t('contactInfo.notFound')}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">
      {children}
    </h3>
  );
}

function ChannelAttributionWarning({
  includeAliasNote = false,
  nameOnly = false,
  className = 'mx-5 my-3 px-3 py-2 rounded-md bg-warning/10 border border-warning/20',
}: {
  includeAliasNote?: boolean;
  nameOnly?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className={className}>
      <p className="text-xs text-warning">
        {t('contactInfo.attribution', {
          kind: nameOnly ? t('contactInfo.attributionName') : t('contactInfo.attributionContact'),
        })}
        {includeAliasNote && t('contactInfo.attributionAlias')}
      </p>
    </div>
  );
}

function MessageStatsSection({
  dmMessageCount,
  channelMessageCount,
  showDirectMessages = true,
}: {
  dmMessageCount: number;
  channelMessageCount: number;
  showDirectMessages?: boolean;
}) {
  const { t } = useTranslation();
  if ((showDirectMessages ? dmMessageCount : 0) <= 0 && channelMessageCount <= 0) {
    return null;
  }

  return (
    <div className="px-5 py-3 border-b border-border">
      <SectionLabel>{t('contactInfo.messages')}</SectionLabel>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        {showDirectMessages && dmMessageCount > 0 && (
          <InfoItem label={t('contactInfo.directMessages')} value={formatNumber(dmMessageCount)} />
        )}
        {channelMessageCount > 0 && (
          <InfoItem
            label={t('contactInfo.channelMessages')}
            value={formatNumber(channelMessageCount)}
          />
        )}
      </div>
    </div>
  );
}

function MostActiveChannelsSection({
  channels,
  onNavigateToChannel,
}: {
  channels: ContactActiveRoom[];
  onNavigateToChannel?: (channelKey: string) => void;
}) {
  const { t } = useTranslation();
  if (channels.length === 0) {
    return null;
  }

  return (
    <div className="px-5 py-3 border-b border-border">
      <SectionLabel>{t('contactInfo.mostActive')}</SectionLabel>
      <div className="space-y-1">
        {channels.map((channel) => (
          <div key={channel.channel_key} className="flex justify-between items-center text-sm">
            <span
              className={
                onNavigateToChannel
                  ? 'cursor-pointer hover:text-primary transition-colors truncate'
                  : 'truncate'
              }
              role={onNavigateToChannel ? 'button' : undefined}
              tabIndex={onNavigateToChannel ? 0 : undefined}
              onKeyDown={onNavigateToChannel ? handleKeyboardActivate : undefined}
              onClick={() => onNavigateToChannel?.(channel.channel_key)}
            >
              {channel.channel_name.startsWith('#') || isPublicChannelKey(channel.channel_key)
                ? channel.channel_name
                : `#${channel.channel_name}`}
            </span>
            <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
              {t('contactInfo.msgCount', { count: channel.message_count })}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ActivityChartsSection({
  analytics,
  ready,
}: {
  analytics: ContactAnalytics | null;
  ready: boolean;
}) {
  const { t } = useTranslation();
  if (!analytics) {
    return null;
  }

  const hasHourlyActivity = analytics.hourly_activity.some(
    (bucket) =>
      bucket.last_24h_count > 0 || bucket.last_week_average > 0 || bucket.all_time_average > 0
  );
  const hasWeeklyActivity = analytics.weekly_activity.some((bucket) => bucket.message_count > 0);
  if (!hasHourlyActivity && !hasWeeklyActivity) {
    return null;
  }

  return (
    <div className="px-5 py-3 border-b border-border space-y-4">
      {hasHourlyActivity && (
        <div>
          <SectionLabel>{t('contactInfo.perHour')}</SectionLabel>
          <ActivityLineChart
            ready={ready}
            ariaLabel={t('contactInfo.perHourAria')}
            points={analytics.hourly_activity}
            series={[
              { key: 'last_24h_count', color: '#2563eb', label: t('contactInfo.last24h') },
              { key: 'last_week_average', color: '#ea580c', label: t('contactInfo.avg7d') },
              { key: 'all_time_average', color: '#64748b', label: t('contactInfo.avgAll') },
            ]}
            legendItems={[
              { label: t('contactInfo.last24h'), color: '#2563eb' },
              { label: t('contactInfo.avg7d'), color: '#ea580c' },
              { label: t('contactInfo.avgAll'), color: '#64748b' },
            ]}
            valueFormatter={(value) => value.toFixed(value % 1 === 0 ? 0 : 1)}
            tickFormatter={(bucket) =>
              new Date(bucket.bucket_start * 1000).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })
            }
          />
        </div>
      )}

      {hasWeeklyActivity && (
        <div>
          <SectionLabel>{t('contactInfo.perWeek')}</SectionLabel>
          <ActivityLineChart
            ready={ready}
            ariaLabel={t('contactInfo.perWeekAria')}
            points={analytics.weekly_activity}
            series={[
              { key: 'message_count', color: '#16a34a', label: t('contactInfo.messagesSeries') },
            ]}
            valueFormatter={(value) => value.toFixed(0)}
            tickFormatter={(bucket) =>
              new Date(bucket.bucket_start * 1000).toLocaleDateString([], {
                month: 'short',
                day: 'numeric',
              })
            }
          />
        </div>
      )}

      <p className="text-[0.6875rem] text-muted-foreground">
        {t('contactInfo.hourlyHelp')}
        {!analytics.includes_direct_messages && ` ${t('contactInfo.nameOnlyHelp')}`}
      </p>
    </div>
  );
}

const TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: 'hsl(var(--popover))',
    border: '1px solid hsl(var(--border))',
    borderRadius: '6px',
    fontSize: '11px',
    color: 'hsl(var(--popover-foreground))',
  },
  itemStyle: { color: 'hsl(var(--popover-foreground))' },
  labelStyle: { color: 'hsl(var(--muted-foreground))' },
} as const;

const ACTIVITY_CHART_HEIGHT = 140;

function ActivityLineChart<T extends ContactAnalyticsHourlyBucket | ContactAnalyticsWeeklyBucket>({
  ready,
  ariaLabel,
  points,
  series,
  legendItems,
  tickFormatter,
  valueFormatter,
}: {
  ready: boolean;
  ariaLabel: string;
  points: T[];
  series: Array<{ key: keyof T; color: string; label?: string }>;
  legendItems?: Array<{ label: string; color: string }>;
  tickFormatter: (point: T) => string;
  valueFormatter: (value: number) => string;
}) {
  // Reserve the chart's height while the pane animates in (see #317).
  if (!ready) {
    return <div role="img" aria-label={ariaLabel} style={{ height: ACTIVITY_CHART_HEIGHT }} />;
  }

  const data = points.map((point, i) => {
    const entry: Record<string, string | number> = { idx: i, tick: tickFormatter(point) };
    for (const s of series) {
      const raw = point[s.key];
      entry[String(s.key)] = typeof raw === 'number' ? raw : 0;
    }
    return entry;
  });

  const tickCount = Math.min(5, points.length);
  const tickIndices: number[] = [];
  if (points.length > 1) {
    for (let i = 0; i < tickCount; i++) {
      tickIndices.push(Math.round((i / (tickCount - 1)) * (points.length - 1)));
    }
  }

  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={ACTIVITY_CHART_HEIGHT}>
        <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
          <XAxis
            dataKey="idx"
            type="number"
            domain={[0, Math.max(1, points.length - 1)]}
            tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
            tickLine={false}
            axisLine={false}
            ticks={tickIndices}
            tickFormatter={(idx) => String(data[idx]?.tick ?? '')}
          />
          <YAxis
            tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => valueFormatter(v)}
            width={40}
          />
          <RechartsTooltip
            {...TOOLTIP_STYLE}
            cursor={{
              stroke: 'hsl(var(--muted-foreground))',
              strokeWidth: 1,
              strokeDasharray: '3 3',
            }}
            labelFormatter={(idx) => String(data[Number(idx)]?.tick ?? '')}
            formatter={(value, name) => {
              const match = series.find((s) => String(s.key) === name);
              return [valueFormatter(Number(value)), match?.label ?? String(name)];
            }}
          />
          {legendItems && (
            <Legend
              content={() => (
                <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 mt-1 text-[0.6875rem] text-muted-foreground">
                  {legendItems.map((item) => (
                    <span key={item.label} className="inline-flex items-center gap-1.5">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ backgroundColor: item.color }}
                      />
                      {item.label}
                    </span>
                  ))}
                </div>
              )}
            />
          )}
          {series.map((entry) => (
            <Line
              key={String(entry.key)}
              type="linear"
              dataKey={String(entry.key)}
              stroke={entry.color}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: 'hsl(var(--popover))' }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function NearbyRepeatersSection({
  contact,
  contacts,
  distanceUnit,
}: {
  contact: Contact;
  contacts: Contact[];
  distanceUnit: import('../utils/distanceUnits').DistanceUnit;
}) {
  const nearby = useMemo(() => {
    const sevenDaysAgo = Math.floor(Date.now() / 1000) - 7 * 86400;
    const results: Array<{ name: string; publicKey: string; distance: number }> = [];
    for (const other of contacts) {
      const heardAt = Math.max(other.last_seen ?? 0, other.last_advert ?? 0);
      if (
        other.public_key === contact.public_key ||
        other.type !== CONTACT_TYPE_REPEATER ||
        !isValidLocation(other.lat, other.lon) ||
        heardAt < sevenDaysAgo
      ) {
        continue;
      }
      const dist = calculateDistance(contact.lat, contact.lon, other.lat, other.lon);
      if (dist !== null) {
        results.push({
          name: getContactDisplayName(other.name, other.public_key, other.last_advert),
          publicKey: other.public_key,
          distance: dist,
        });
      }
    }
    results.sort((a, b) => a.distance - b.distance);
    return results.slice(0, 5);
  }, [contact.public_key, contact.lat, contact.lon, contacts]);

  const { t } = useTranslation();
  if (nearby.length === 0) return null;

  return (
    <div className="px-5 py-3 border-b border-border">
      <SectionLabel>{t('contactInfo.nearestGeo')}</SectionLabel>
      <div className="space-y-1">
        {nearby.map((r) => (
          <div key={r.publicKey} className="flex justify-between items-center text-sm">
            <span className="truncate">{r.name}</span>
            <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
              {formatDistance(r.distance, distanceUnit)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function InfoItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <span className="text-muted-foreground text-xs">{label}</span>
      <p className="font-medium text-sm leading-tight">{value}</p>
    </div>
  );
}

// Stable color rotation for dynamic LPP sensors in the history chart
const LPP_CHART_COLORS = ['#22c55e', '#8b5cf6', '#0ea5e9', '#ef4444', '#f59e0b', '#ec4899'];

function ContactTelemetrySection({
  contact,
  loading,
  onFetch,
  telemetryHistory,
  isTracked,
  onToggleTracked,
}: {
  contact: Contact;
  loading: boolean;
  onFetch: () => void;
  telemetryHistory: TelemetryHistoryEntry[];
  isTracked: boolean;
  onToggleTracked?: (publicKey: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();
  const [expanded, setExpanded] = useState(true);
  const [mapExpanded, setMapExpanded] = useState(false);
  const [chartExpanded, setChartExpanded] = useState(false);
  const [toggling, setToggling] = useState(false);

  // Latest telemetry snapshot from history
  const latestEntry =
    telemetryHistory.length > 0 ? telemetryHistory[telemetryHistory.length - 1] : null;
  const sensors: LppSensor[] = useMemo(() => {
    if (!latestEntry?.data?.lpp_sensors) return [];
    return latestEntry.data.lpp_sensors.map((s: TelemetryLppSensor) => ({
      channel: s.channel,
      type_name: s.type_name,
      value: s.value,
    }));
  }, [latestEntry]);
  const fetchedAt = latestEntry?.timestamp ?? null;

  // Extract GPS from sensors
  const gpsSensor = sensors.find(
    (s) => s.type_name === 'gps' && typeof s.value === 'object' && s.value !== null
  );
  const gpsValue = gpsSensor?.value as Record<string, number> | undefined;
  const hasGps =
    gpsValue != null &&
    typeof gpsValue.latitude === 'number' &&
    typeof gpsValue.longitude === 'number';

  // Non-GPS sensors for display
  const displaySensors = sensors.filter((s) => s.type_name !== 'gps');

  // Build disambiguated labels
  const labels = useMemo(() => {
    const counts = new Map<string, number>();
    return displaySensors.map((s) => {
      const base = `${s.type_name}_${s.channel}`;
      const n = (counts.get(base) ?? 0) + 1;
      counts.set(base, n);
      return formatLppLabel(s.type_name) + (n > 1 ? ` (${n})` : '');
    });
  }, [displaySensors]);

  // Discover unique LPP sensor series from history for charting
  const sensorSeries = useMemo(() => {
    const seen = new Map<string, { type_name: string; channel: number }>();
    for (const entry of telemetryHistory) {
      for (const s of entry.data?.lpp_sensors ?? []) {
        if (typeof s.value !== 'number') continue;
        const key = `${s.type_name}_ch${s.channel}`;
        if (!seen.has(key)) seen.set(key, { type_name: s.type_name, channel: s.channel });
      }
    }
    return Array.from(seen.entries()).map(([key, info], i) => ({
      key,
      label: formatLppLabel(info.type_name),
      color: LPP_CHART_COLORS[i % LPP_CHART_COLORS.length],
      ...info,
    }));
  }, [telemetryHistory]);

  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const activeMetric = selectedMetric ?? (sensorSeries.length > 0 ? sensorSeries[0].key : null);

  // Build chart data for selected metric
  const chartData = useMemo(() => {
    if (!activeMetric) return [];
    const series = sensorSeries.find((s) => s.key === activeMetric);
    if (!series) return [];
    return telemetryHistory
      .filter((e) => e.data?.lpp_sensors)
      .map((e) => {
        const sensor = (e.data.lpp_sensors ?? []).find(
          (s: TelemetryLppSensor) =>
            s.type_name === series.type_name && s.channel === series.channel
        );
        return {
          time: e.timestamp,
          value: sensor && typeof sensor.value === 'number' ? sensor.value : null,
        };
      })
      .filter((d) => d.value !== null);
  }, [telemetryHistory, activeMetric, sensorSeries]);

  const activeSeries = sensorSeries.find((s) => s.key === activeMetric);

  return (
    <div className="px-5 py-3 border-b border-border">
      <div className="flex items-center justify-between">
        <button
          type="button"
          className="flex items-center gap-1.5 text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          {t('contactInfo.telemetry')}
        </button>
        <button
          type="button"
          onClick={onFetch}
          disabled={loading}
          className="text-xs px-2 py-0.5 rounded border border-border hover:bg-accent disabled:opacity-50 transition-colors flex items-center gap-1"
        >
          <Activity className="h-3 w-3" />
          {loading ? t('contactInfo.fetching') : t('contactInfo.request')}
        </button>
      </div>

      {expanded && (
        <div className="mt-2">
          {sensors.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              {fetchedAt ? t('contactInfo.noSensorData') : t('contactInfo.notFetched')}
            </p>
          ) : (
            <>
              <div className="space-y-0.5">
                {displaySensors.map((sensor, i) => (
                  <LppSensorRow
                    key={`${sensor.type_name}-${sensor.channel}-${i}`}
                    sensor={sensor}
                    unitPref={distanceUnit}
                    label={labels[i]}
                  />
                ))}
              </div>

              {hasGps && (
                <div className="mt-2">
                  <button
                    type="button"
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-primary transition-colors"
                    onClick={() => setMapExpanded(!mapExpanded)}
                  >
                    {mapExpanded ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    GPS: {gpsValue!.latitude.toFixed(5)}, {gpsValue!.longitude.toFixed(5)}
                  </button>
                  {mapExpanded && (
                    <div className="mt-1 h-48 rounded border border-border overflow-hidden">
                      <MapContainer
                        center={[gpsValue!.latitude, gpsValue!.longitude]}
                        zoom={13}
                        className="h-full w-full"
                        style={{ background: '#1a1a2e' }}
                      >
                        <TileLayer
                          attribution={OSM_RASTER_TILE_ATTRIBUTION}
                          url={OSM_RASTER_TILE_URL}
                          referrerPolicy={OSM_RASTER_REFERRER_POLICY}
                        />
                        <CircleMarker
                          center={[gpsValue!.latitude, gpsValue!.longitude]}
                          radius={7}
                          pathOptions={{
                            color: '#1d4ed8',
                            fillColor: '#3b82f6',
                            fillOpacity: 1,
                            weight: 2,
                          }}
                        >
                          <Popup>
                            <span className="text-sm">
                              {contact.name ?? contact.public_key.slice(0, 12)}
                            </span>
                          </Popup>
                        </CircleMarker>
                      </MapContainer>
                    </div>
                  )}
                </div>
              )}

              {fetchedAt && (
                <p className="text-[0.6875rem] text-muted-foreground mt-1.5">
                  {t('contactInfo.fetchedAt', { time: formatTime(fetchedAt) })}
                </p>
              )}
            </>
          )}

          {/* History chart */}
          {telemetryHistory.length > 1 && sensorSeries.length > 0 && (
            <div className="mt-2">
              <button
                type="button"
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-primary transition-colors"
                onClick={() => setChartExpanded(!chartExpanded)}
              >
                {chartExpanded ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
                {t('contactInfo.historySamples', { count: telemetryHistory.length })}
              </button>
              {chartExpanded && (
                <div className="mt-1">
                  <div className="flex flex-wrap gap-1 mb-2">
                    {sensorSeries.map((s) => (
                      <button
                        key={s.key}
                        type="button"
                        onClick={() => setSelectedMetric(s.key)}
                        className={`text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded transition-colors ${
                          activeMetric === s.key
                            ? 'bg-primary/10 text-primary'
                            : 'bg-muted text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                  {chartData.length > 1 && activeSeries && (
                    <ResponsiveContainer width="100%" height={120}>
                      <AreaChart data={chartData}>
                        <CartesianGrid
                          strokeDasharray="3 3"
                          stroke="hsl(var(--border))"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="time"
                          tickFormatter={(t: number) => {
                            const d = new Date(t * 1000);
                            return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${d.getMinutes().toString().padStart(2, '0')}`;
                          }}
                          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                          tickLine={false}
                          axisLine={false}
                          width={40}
                        />
                        <RechartsTooltip
                          {...TOOLTIP_STYLE}
                          labelFormatter={(t) => new Date(Number(t) * 1000).toLocaleString()}
                        />
                        <Area
                          type="monotone"
                          dataKey="value"
                          name={activeSeries.label}
                          stroke={activeSeries.color}
                          fill={activeSeries.color}
                          fillOpacity={0.15}
                          dot={false}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Tracking toggle */}
          {onToggleTracked && (
            <div className="mt-2 pt-2 border-t border-border/50">
              <button
                type="button"
                disabled={toggling}
                onClick={async () => {
                  setToggling(true);
                  try {
                    await onToggleTracked(contact.public_key);
                  } finally {
                    setToggling(false);
                  }
                }}
                className={`text-xs px-2 py-1 rounded border transition-colors w-full ${
                  isTracked
                    ? 'border-destructive/50 text-destructive hover:bg-destructive/10'
                    : 'border-green-600/50 text-green-600 hover:bg-green-600/10'
                } disabled:opacity-50`}
              >
                {toggling
                  ? t('contactInfo.updating')
                  : isTracked
                    ? t('contactInfo.stopTracking')
                    : t('contactInfo.startTracking')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
