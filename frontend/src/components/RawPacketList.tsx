import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useVirtualizer } from '@tanstack/react-virtual';
import { MeshCoreDecoder, PayloadType } from '@michaelhart/meshcore-decoder';
import { Lock, LockOpen } from 'lucide-react';
import type { Channel, Contact, Conversation, RawPacket } from '../types';
import {
  useRawPacketDerivedCache,
  type RawPacketDerivedEntry,
  type RawPacketDerivedScope,
} from '../utils/rawPacketDerivedCache';
import { createDecoderOptions, isCleartextPayloadType } from '../utils/rawPacketInspector';
import { PAYLOAD_TYPE_COLORS, type KnownPayloadType } from '../utils/rawPacketStats';
import { labelPayloadType } from '../utils/rawPacketLabels';
import { getContactDisplayName } from '../utils/pubkey';
import { cn } from '@/lib/utils';

export interface PacketNavHints {
  advertPublicKey: string | null;
  advertKnown: boolean;
  advertHasGps: boolean;
  groupTextChannelKey: string | null;
  groupTextChannelName: string | null;
  destHash: string | null;
  srcHash: string | null;
}

interface RawPacketListProps {
  packets: RawPacket[];
  /** When the radio is down, an empty feed is expected rather than a wait. */
  radioOffline?: boolean;
  channels?: Channel[];
  contacts?: Contact[];
  extraSecrets?: string[];
  onPacketClick?: (packet: RawPacket) => void;
  onRepeatFilter?: (hash: string) => void;
  /** When true (default), the feed sticks to the newest packet. */
  autoScroll?: boolean;
  /** Window the list when the display cap is above 500. */
  virtualize?: boolean;
  onOpenContactInfo?: (publicKey: string) => void;
  onSelectConversation?: (conversation: Conversation) => void;
  onOpenVisualizer?: (packet: RawPacket) => void;
  onUnknownAdvert?: () => void;
  onUnresolvedHash?: (hash: string) => void;
  onPinAdvert?: (publicKey: string) => void;
  onCopyHex?: (packet: RawPacket) => void;
  onCopyJson?: (packet: RawPacket, includeCleartext: boolean) => void;
  /** Live-only visualizer handoff. Hidden when replay lands. */
  showVisualizerAction?: boolean;
  /** Live uses `obs-*` / `db-*`. Replay uses `hist-{id}`. */
  derivedScope?: RawPacketDerivedScope;
  /** Overrides the default empty-feed copy (replay has its own). */
  emptyMessage?: string;
}

export function packetRepeatKey(packet: RawPacket): string {
  return packet.packet_hash || String(packet.id);
}

export function resolveContactsByPrefix(hash: string, contacts: Contact[]): Contact[] {
  const needle = hash
    .trim()
    .replace(/^hash1:/i, '')
    .toLowerCase();
  if (!needle) return [];
  return contacts.filter((contact) => contact.public_key.toLowerCase().startsWith(needle));
}

function findContactByKey(publicKey: string, contacts: Contact[]): Contact | undefined {
  const lower = publicKey.toLowerCase();
  return contacts.find((contact) => contact.public_key.toLowerCase() === lower);
}

export function extractPacketNavHints(
  packet: RawPacket,
  contacts: Contact[],
  channels: Channel[],
  derived?: Pick<RawPacketDerivedEntry, 'payloadType' | 'isOpen'>
): PacketNavHints {
  const payloadType = derived?.payloadType ?? packet.payload_type;
  const isOpen = derived?.isOpen ?? false;
  const hints: PacketNavHints = {
    advertPublicKey: null,
    advertKnown: false,
    advertHasGps: false,
    groupTextChannelKey: null,
    groupTextChannelName: null,
    destHash: null,
    srcHash: null,
  };

  try {
    const decoded = MeshCoreDecoder.decode(packet.data, createDecoderOptions(channels));
    if (decoded.isValid && decoded.payload.decoded) {
      const payload = decoded.payload.decoded as {
        publicKey?: string;
        destinationHash?: string;
        sourceHash?: string;
        appData?: { hasLocation?: boolean; location?: { latitude?: number; longitude?: number } };
      };
      if (decoded.payloadType === PayloadType.Advert && payload.publicKey) {
        const contact = findContactByKey(payload.publicKey, contacts);
        hints.advertPublicKey = contact?.public_key ?? payload.publicKey;
        hints.advertKnown = contact != null;
        hints.advertHasGps = Boolean(
          payload.appData?.hasLocation &&
          payload.appData.location &&
          Number.isFinite(payload.appData.location.latitude) &&
          Number.isFinite(payload.appData.location.longitude)
        );
      }
      if (payload.destinationHash) {
        hints.destHash = payload.destinationHash.toUpperCase();
      }
      if (payload.sourceHash) {
        hints.srcHash = payload.sourceHash.toUpperCase();
      }
    }
  } catch {
    // Decoder failures still allow decrypted_info fallbacks below.
  }

  if (!hints.advertPublicKey && packet.decrypted_info?.contact_key && /advert/i.test(payloadType)) {
    const contact = findContactByKey(packet.decrypted_info.contact_key, contacts);
    hints.advertPublicKey = contact?.public_key ?? packet.decrypted_info.contact_key;
    hints.advertKnown = contact != null;
  }

  if (isOpen && /grouptext/i.test(payloadType.replace(/[\s_-]+/g, ''))) {
    const infoKey = packet.decrypted_info?.channel_key;
    if (infoKey) {
      const match = channels.find((channel) => channel.key.toLowerCase() === infoKey.toLowerCase());
      hints.groupTextChannelKey = match?.key ?? infoKey;
      hints.groupTextChannelName = match?.name ?? packet.decrypted_info?.channel_name ?? infoKey;
    } else if (channels.length === 1) {
      hints.groupTextChannelKey = channels[0].key;
      hints.groupTextChannelName = channels[0].name;
    }
  }

  return hints;
}

export function isZeroHopPacket(packet: RawPacket): boolean {
  try {
    const decoded = MeshCoreDecoder.decode(packet.data);
    if (!decoded.isValid) return false;
    const path = decoded.path ?? [];
    return (decoded.pathLength ?? path.length) === 0;
  } catch {
    return false;
  }
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatSignalInfo(packet: RawPacket): string {
  const parts: string[] = [];
  if (packet.snr !== null && packet.snr !== undefined) {
    parts.push(`SNR: ${packet.snr.toFixed(1)} dB`);
  }
  if (packet.rssi !== null && packet.rssi !== undefined) {
    parts.push(`RSSI: ${packet.rssi} dBm`);
  }
  return parts.join(' | ');
}

function getRouteTypeColor(routeType: string): string {
  switch (routeType) {
    case 'Flood':
      return 'bg-info/20 text-info';
    case 'Direct':
      return 'bg-success/20 text-success';
    case 'TransportFlood':
      return 'bg-purple-500/20 text-purple-400';
    case 'TransportDirect':
      return 'bg-orange-500/20 text-orange-400';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

function getRouteTypeLabel(routeType: string): string {
  switch (routeType) {
    case 'Flood':
      return 'F';
    case 'Direct':
      return 'D';
    case 'TransportFlood':
      return 'TF';
    case 'TransportDirect':
      return 'TD';
    default:
      return '?';
  }
}

function payloadTypeColor(payloadType: string): string {
  return PAYLOAD_TYPE_COLORS[payloadType as KnownPayloadType] ?? PAYLOAD_TYPE_COLORS.Unknown;
}

export function RawPacketList({
  packets,
  channels,
  contacts = [],
  extraSecrets,
  onPacketClick,
  onRepeatFilter,
  autoScroll = true,
  radioOffline = false,
  virtualize = false,
  onOpenContactInfo,
  onSelectConversation,
  onOpenVisualizer,
  onUnknownAdvert,
  onUnresolvedHash,
  onPinAdvert,
  onCopyHex,
  onCopyJson,
  showVisualizerAction = true,
  derivedScope = 'live',
  emptyMessage,
}: RawPacketListProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const [hoveredRepeatKey, setHoveredRepeatKey] = useState<string | null>(null);
  const [openCopyKey, setOpenCopyKey] = useState<string | null>(null);
  const [includeCleartext, setIncludeCleartext] = useState(false);
  const [hashPicker, setHashPicker] = useState<{
    observationKey: string;
    hash: string;
    matches: Contact[];
  } | null>(null);

  const derivedPackets = useRawPacketDerivedCache(packets, {
    channels,
    extraSecrets,
    scope: derivedScope,
  });

  const repeatCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of derivedPackets) {
      const key = packetRepeatKey(entry.packet);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [derivedPackets]);

  const sortedPackets = useMemo(
    () => [...derivedPackets].sort((a, b) => a.packet.timestamp - b.packet.timestamp),
    [derivedPackets]
  );

  const virtualizer = useVirtualizer({
    count: virtualize ? sortedPackets.length : 0,
    getScrollElement: () => listRef.current,
    estimateSize: () => 148,
    overscan: 8,
    getItemKey: (index) => sortedPackets[index].cacheKey,
  });

  useEffect(() => {
    if (autoScroll && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [packets, autoScroll]);

  const handleHashClick = (hash: string, observationKey: string) => {
    const matches = resolveContactsByPrefix(hash, contacts);
    if (matches.length === 1) {
      setHashPicker(null);
      onOpenContactInfo?.(matches[0].public_key);
      return;
    }
    if (matches.length === 0) {
      setHashPicker(null);
      onUnresolvedHash?.(hash);
      return;
    }
    setHashPicker({ observationKey, hash, matches });
  };

  const handleAdvertClick = (hints: PacketNavHints) => {
    if (hints.advertPublicKey && hints.advertKnown) {
      onOpenContactInfo?.(hints.advertPublicKey);
      return;
    }
    onUnknownAdvert?.();
  };

  const renderCard = (entry: RawPacketDerivedEntry) => {
    const { packet, payloadType, routeType, summary, isOpen } = entry;
    const repeatKey = packetRepeatKey(packet);
    const observationKey = entry.cacheKey;
    const repeatCount = repeatCounts.get(repeatKey) ?? 1;
    const typeColor = payloadTypeColor(payloadType);
    const showLock = !isCleartextPayloadType(payloadType);
    const hints = extractPacketNavHints(packet, contacts, channels ?? [], entry);
    const highlighted = hoveredRepeatKey != null && hoveredRepeatKey === repeatKey;
    const cardContent = (
      <>
        <div className="flex items-center gap-2">
          <span
            className={`text-[0.625rem] font-mono px-1.5 py-0.5 rounded ${getRouteTypeColor(routeType)}`}
            title={routeType}
          >
            {getRouteTypeLabel(routeType)}
          </span>

          <span
            className="text-[0.625rem] font-mono px-1.5 py-0.5 rounded"
            style={{ backgroundColor: `${typeColor}33`, color: typeColor }}
            title={payloadType}
          >
            {labelPayloadType(payloadType)}
          </span>

          {showLock ? (
            isOpen ? (
              <>
                <LockOpen className="h-3 w-3 text-success" aria-hidden="true" />
                <span className="sr-only">{t('rawPacket.decryptedOpen')}</span>
              </>
            ) : (
              <>
                <Lock className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
                <span className="sr-only">{t('rawPacket.encrypted')}</span>
              </>
            )
          ) : null}

          <span className={cn('text-[0.8125rem]', isOpen ? 'text-primary' : 'text-foreground')}>
            {summary}
          </span>

          <span className="text-muted-foreground ml-auto text-xs tabular-nums">
            {formatTime(packet.timestamp)}
          </span>
        </div>

        {(packet.snr !== null || packet.rssi !== null) && (
          <div className="text-[0.6875rem] text-muted-foreground mt-0.5 tabular-nums">
            {formatSignalInfo(packet)}
          </div>
        )}

        <div className="font-mono text-[0.625rem] break-all text-muted-foreground mt-1.5 p-1.5 bg-background/60 rounded">
          {packet.data.toUpperCase()}
        </div>
      </>
    );

    const actionRow = (
      <>
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {hints.advertPublicKey && (hints.advertKnown ? onOpenContactInfo : onUnknownAdvert) ? (
            <button
              type="button"
              className="text-[0.625rem] rounded px-1.5 py-0.5 bg-muted text-muted-foreground hover:text-foreground"
              onClick={(event) => {
                event.stopPropagation();
                handleAdvertClick(hints);
              }}
            >
              {t('rawPacket.openContact')}
            </button>
          ) : null}
          {hints.advertPublicKey && hints.advertHasGps && onPinAdvert ? (
            <button
              type="button"
              className="text-[0.625rem] rounded px-1.5 py-0.5 bg-muted text-muted-foreground hover:text-foreground"
              onClick={(event) => {
                event.stopPropagation();
                onPinAdvert(hints.advertPublicKey!);
              }}
            >
              {t('rawPacket.pinOnMap')}
            </button>
          ) : null}
          {hints.groupTextChannelKey && onSelectConversation ? (
            <button
              type="button"
              className="text-[0.625rem] rounded px-1.5 py-0.5 bg-muted text-muted-foreground hover:text-foreground"
              onClick={(event) => {
                event.stopPropagation();
                onSelectConversation({
                  type: 'channel',
                  id: hints.groupTextChannelKey!,
                  name: hints.groupTextChannelName ?? hints.groupTextChannelKey!,
                });
              }}
            >
              {t('rawPacket.openChannel')}
            </button>
          ) : null}
          {hints.destHash && (onOpenContactInfo || onUnresolvedHash) ? (
            <button
              type="button"
              className="text-[0.625rem] rounded px-1.5 py-0.5 bg-muted text-muted-foreground hover:text-foreground"
              onClick={(event) => {
                event.stopPropagation();
                handleHashClick(hints.destHash!, observationKey);
              }}
            >
              {t('rawPacket.destHash', { hash: hints.destHash })}
            </button>
          ) : null}
          {hints.srcHash && (onOpenContactInfo || onUnresolvedHash) ? (
            <button
              type="button"
              className="text-[0.625rem] rounded px-1.5 py-0.5 bg-muted text-muted-foreground hover:text-foreground"
              onClick={(event) => {
                event.stopPropagation();
                handleHashClick(hints.srcHash!, observationKey);
              }}
            >
              {t('rawPacket.srcHash', { hash: hints.srcHash })}
            </button>
          ) : null}
          {showVisualizerAction && onOpenVisualizer ? (
            <button
              type="button"
              className="text-[0.625rem] rounded px-1.5 py-0.5 bg-muted text-muted-foreground hover:text-foreground"
              onClick={(event) => {
                event.stopPropagation();
                onOpenVisualizer(packet);
              }}
            >
              {t('rawPacket.openVisualizer')}
            </button>
          ) : null}
          {onCopyHex || onCopyJson ? (
            <div className="relative">
              <button
                type="button"
                className="text-[0.625rem] rounded px-1.5 py-0.5 bg-muted text-muted-foreground hover:text-foreground"
                aria-label={t('rawPacket.copyMenuAria')}
                aria-expanded={openCopyKey === observationKey}
                onClick={(event) => {
                  event.stopPropagation();
                  setOpenCopyKey((current) => (current === observationKey ? null : observationKey));
                }}
              >
                {t('rawPacket.copyMenu')}
              </button>
              {openCopyKey === observationKey ? (
                <div
                  className="absolute left-0 z-20 mt-1 min-w-[12rem] rounded-md border border-border bg-popover p-1.5 text-popover-foreground shadow-md"
                  onClick={(event) => event.stopPropagation()}
                >
                  <button
                    type="button"
                    className="block w-full rounded px-2 py-1 text-left text-xs hover:bg-accent"
                    onClick={() => {
                      onCopyHex?.(packet);
                      setOpenCopyKey(null);
                    }}
                  >
                    {t('rawPacket.copyHex')}
                  </button>
                  <button
                    type="button"
                    className="block w-full rounded px-2 py-1 text-left text-xs hover:bg-accent"
                    onClick={() => {
                      onCopyJson?.(packet, includeCleartext);
                      setOpenCopyKey(null);
                    }}
                  >
                    {t('rawPacket.copyJson')}
                  </button>
                  <label className="mt-1 flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={includeCleartext}
                      onChange={(event) => setIncludeCleartext(event.target.checked)}
                    />
                    {t('rawPacket.copyJsonCleartext')}
                  </label>
                  {includeCleartext ? (
                    <p className="px-2 pb-1 text-[0.625rem] text-warning">
                      {t('rawPacket.copyJsonCleartextWarning')}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        {hashPicker?.observationKey === observationKey ? (
          <div
            className="mt-1.5 rounded-md border border-border bg-popover p-1.5 text-popover-foreground"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="px-1.5 pb-1 text-[0.625rem] uppercase tracking-wider text-muted-foreground">
              {t('rawPacket.hashPicker')}
            </div>
            {hashPicker.matches.map((contact) => (
              <button
                key={contact.public_key}
                type="button"
                className="block w-full rounded px-2 py-1 text-left text-xs hover:bg-accent"
                onClick={() => {
                  onOpenContactInfo?.(contact.public_key);
                  setHashPicker(null);
                }}
              >
                {getContactDisplayName(contact.name, contact.public_key, contact.last_advert)}
              </button>
            ))}
          </div>
        ) : null}
      </>
    );

    const className = cn(
      'min-w-0 w-full rounded-md border border-border/50 bg-card px-3 py-2 text-left',
      onPacketClick &&
        'cursor-pointer transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
    );

    const repeatBadge =
      repeatCount > 1 ? (
        onRepeatFilter ? (
          <button
            type="button"
            className="mt-2 shrink-0 self-start text-[0.625rem] font-mono px-1.5 py-0.5 rounded bg-muted text-muted-foreground hover:text-foreground"
            onClick={() => onRepeatFilter(repeatKey)}
            aria-label={t('rawPacket.repeatFilterAria', { count: repeatCount })}
          >
            {t('rawPacket.repeatCount', { count: repeatCount })}
          </button>
        ) : (
          <span className="mt-2 shrink-0 self-start text-[0.625rem] font-mono px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
            {t('rawPacket.repeatCount', { count: repeatCount })}
          </span>
        )
      ) : null;

    return (
      <div
        key={observationKey}
        className="flex items-start gap-1"
        data-repeat-key={repeatKey}
        onMouseEnter={() => setHoveredRepeatKey(repeatKey)}
        onMouseLeave={() =>
          setHoveredRepeatKey((current) => (current === repeatKey ? null : current))
        }
      >
        <div className={cn('min-w-0 flex-1', highlighted && 'rounded-md ring-2 ring-primary')}>
          {onPacketClick ? (
            <div
              role="button"
              tabIndex={0}
              onClick={() => onPacketClick(packet)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onPacketClick(packet);
                }
              }}
              className={className}
            >
              {cardContent}
            </div>
          ) : (
            <div className={className}>{cardContent}</div>
          )}
          {actionRow}
        </div>
        {repeatBadge}
      </div>
    );
  };

  if (packets.length === 0) {
    return (
      <div className="h-full overflow-y-auto p-5 text-center text-muted-foreground [contain:layout_paint]">
        {radioOffline ? (
          <>
            <p>{t('rawPacket.emptyRadioOffline')}</p>
            <p className="mt-1 text-xs">{t('rawPacket.emptyRadioOfflineHint')}</p>
          </>
        ) : (
          emptyMessage ?? t('rawPacket.empty')
        )}
      </div>
    );
  }

  return (
    <div
      className="h-full overflow-y-auto p-4 flex flex-col gap-2 [contain:layout_paint]"
      ref={listRef}
    >
      {virtualize ? (
        <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
          {virtualizer.getVirtualItems().map((item) => (
            <div
              key={item.key}
              className="absolute left-0 top-0 w-full pb-2"
              style={{ transform: `translateY(${item.start}px)` }}
            >
              {renderCard(sortedPackets[item.index])}
            </div>
          ))}
        </div>
      ) : (
        sortedPackets.map((entry) => renderCard(entry))
      )}
    </div>
  );
}
