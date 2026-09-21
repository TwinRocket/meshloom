import { useEffect, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Lock, LockOpen } from 'lucide-react';
import type { Channel, RawPacket } from '../types';
import { getRawPacketObservationKey } from '../utils/rawPacketIdentity';
import {
  collectGroupDataKeys,
  createDecoderOptions,
  decodePacketSummary,
  isCleartextPayloadType,
  isPacketOpen,
} from '../utils/rawPacketInspector';
import {
  getPacketTypeName,
  PAYLOAD_TYPE_COLORS,
  type KnownPayloadType,
} from '../utils/rawPacketStats';
import { labelPayloadType } from '../utils/rawPacketLabels';
import { cn } from '@/lib/utils';

interface RawPacketListProps {
  packets: RawPacket[];
  /** When the radio is down, an empty feed is expected rather than a wait. */
  radioOffline?: boolean;
  channels?: Channel[];
  extraSecrets?: string[];
  onPacketClick?: (packet: RawPacket) => void;
  onRepeatFilter?: (hash: string) => void;
  /** When true (default), the feed sticks to the newest packet. */
  autoScroll?: boolean;
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

function packetRepeatKey(packet: RawPacket): string {
  return packet.packet_hash || String(packet.id);
}

// Get route type badge color
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

// Get short route type label
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
  extraSecrets,
  onPacketClick,
  onRepeatFilter,
  autoScroll = true,
  radioOffline = false,
}: RawPacketListProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const decoderOptions = useMemo(() => createDecoderOptions(channels), [channels]);
  const inspectExtras = useMemo(
    () => ({
      channelKeys: collectGroupDataKeys(channels, extraSecrets),
      extraSecrets,
    }),
    [channels, extraSecrets]
  );

  // Decode all packets (memoized to avoid re-decoding on every render)
  const decodedPackets = useMemo(() => {
    return packets.map((packet) => {
      const decoded = decodePacketSummary(packet, decoderOptions, inspectExtras);
      const payloadType = decoded.payloadType || getPacketTypeName(packet, decoderOptions);
      const clientDecoded = decoded.clientDecoded;
      const isOpen = isPacketOpen(payloadType, packet, clientDecoded);
      return {
        packet,
        decoded,
        payloadType,
        isOpen,
      };
    });
  }, [decoderOptions, inspectExtras, packets]);

  const repeatCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const { packet } of decodedPackets) {
      const key = packetRepeatKey(packet);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [decodedPackets]);

  // Sort packets by timestamp ascending (oldest first)
  const sortedPackets = useMemo(
    () => [...decodedPackets].sort((a, b) => a.packet.timestamp - b.packet.timestamp),
    [decodedPackets]
  );

  // Stick to the newest packet while autoscroll is on. Toggling it back on also
  // jumps to the bottom immediately (autoScroll is a dependency).
  useEffect(() => {
    if (autoScroll && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [packets, autoScroll]);

  if (packets.length === 0) {
    // Promising packets "in real time" while nothing can arrive is the kind of
    // empty state that makes someone wait for something that is never coming.
    return (
      <div className="h-full overflow-y-auto p-5 text-center text-muted-foreground [contain:layout_paint]">
        {radioOffline ? (
          <>
            <p>{t('rawPacket.emptyRadioOffline')}</p>
            <p className="mt-1 text-xs">{t('rawPacket.emptyRadioOfflineHint')}</p>
          </>
        ) : (
          t('rawPacket.empty')
        )}
      </div>
    );
  }

  return (
    <div
      className="h-full overflow-y-auto p-4 flex flex-col gap-2 [contain:layout_paint]"
      ref={listRef}
    >
      {sortedPackets.map(({ packet, decoded, payloadType, isOpen }) => {
        const repeatKey = packetRepeatKey(packet);
        const repeatCount = repeatCounts.get(repeatKey) ?? 1;
        const typeColor = payloadTypeColor(payloadType);
        const showLock = !isCleartextPayloadType(payloadType);
        const cardContent = (
          <>
            <div className="flex items-center gap-2">
              {/* Route type badge */}
              <span
                className={`text-[0.625rem] font-mono px-1.5 py-0.5 rounded ${getRouteTypeColor(decoded.routeType)}`}
                title={decoded.routeType}
              >
                {getRouteTypeLabel(decoded.routeType)}
              </span>

              {/* Payload type badge */}
              <span
                className="text-[0.625rem] font-mono px-1.5 py-0.5 rounded"
                style={{ backgroundColor: `${typeColor}33`, color: typeColor }}
                title={payloadType}
              >
                {labelPayloadType(payloadType)}
              </span>

              {/* Encryption status — computed locally; never write packet.decrypted */}
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

              {/* Summary */}
              <span className={cn('text-[0.8125rem]', isOpen ? 'text-primary' : 'text-foreground')}>
                {decoded.summary}
              </span>

              {/* Time */}
              <span className="text-muted-foreground ml-auto text-xs tabular-nums">
                {formatTime(packet.timestamp)}
              </span>
            </div>

            {/* Signal info */}
            {(packet.snr !== null || packet.rssi !== null) && (
              <div className="text-[0.6875rem] text-muted-foreground mt-0.5 tabular-nums">
                {formatSignalInfo(packet)}
              </div>
            )}

            {/* Raw hex data (always visible) */}
            <div className="font-mono text-[0.625rem] break-all text-muted-foreground mt-1.5 p-1.5 bg-background/60 rounded">
              {packet.data.toUpperCase()}
            </div>
          </>
        );

        const className = cn(
          'min-w-0 flex-1 rounded-md border border-border/50 bg-card px-3 py-2 text-left',
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
          <div key={getRawPacketObservationKey(packet)} className="flex items-start gap-1">
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
            {repeatBadge}
          </div>
        );
      })}
    </div>
  );
}
