import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RechartsTooltip } from 'recharts';
import { Star } from 'lucide-react';
import { api } from '../api';
import { formatTime } from '../utils/messageParser';
import { handleKeyboardActivate } from '../utils/a11y';
import { useEntranceSettled } from '../hooks/useEntranceSettled';
import { formatChannelAddUri } from '../utils/meshcoreUri';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { MeshcoreShareQr } from './MeshcoreShareQr';
import { toast } from './ui/sonner';
import type { Channel, ChannelDetail, PathHashWidthStats } from '../types';
import { formatNumber } from '../utils/formatNumber';

interface ChannelInfoPaneProps {
  channelKey: string | null;
  onClose: () => void;
  channels: Channel[];
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
}

export function ChannelInfoPane({
  channelKey,
  onClose,
  channels,
  onToggleFavorite,
}: ChannelInfoPaneProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<ChannelDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [showKey, setShowKey] = useState(false);

  // Get live channel data from channels array (real-time via WS)
  const liveChannel = channelKey ? (channels.find((c) => c.key === channelKey) ?? null) : null;

  // Defer mounting the Recharts pie until the pane's slide-in animation settles;
  // mounting it mid-transform crashes Safari (React #185). See #317.
  const chartReady = useEntranceSettled(channelKey !== null);

  useEffect(() => {
    setShowKey(false);
    if (!channelKey) {
      setDetail(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    api
      .getChannelDetail(channelKey)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) {
          console.error('Failed to fetch channel detail:', err);
          toast.error(t('channelInfo.loadFailed'));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [channelKey]);

  // Use live channel data where available, fall back to detail snapshot
  const channel = liveChannel ?? detail?.channel ?? null;
  const shareUri = channel
    ? formatChannelAddUri({
        name: channel.name,
        secret: channel.key,
        regionScope: channel.flood_scope_override ?? undefined,
      })
    : null;

  return (
    <Sheet open={channelKey !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-[400px] p-0 flex flex-col">
        <SheetHeader className="sr-only">
          <SheetTitle>{t('channelInfo.title')}</SheetTitle>
          <SheetDescription>{t('channelInfo.description')}</SheetDescription>
        </SheetHeader>

        {loading && !detail ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            {t('channelInfo.loading')}
          </div>
        ) : channel ? (
          <div className="flex-1 overflow-y-auto">
            {/* Header */}
            <div className="px-5 pt-5 pb-4 border-b border-border">
              <h2 className="text-lg font-semibold truncate">
                {channel.is_hashtag && !channel.name.startsWith('#')
                  ? `#${channel.name}`
                  : channel.name}
              </h2>
              {!channel.is_hashtag && !showKey ? (
                <button
                  className="text-xs font-mono text-muted-foreground hover:text-primary transition-colors"
                  onClick={() => setShowKey(true)}
                  title={t('channelInfo.revealKey')}
                >
                  {t('channelInfo.showKey')}
                </button>
              ) : (
                <span
                  className="text-xs font-mono text-muted-foreground cursor-pointer hover:text-primary transition-colors block truncate"
                  role="button"
                  tabIndex={0}
                  onKeyDown={handleKeyboardActivate}
                  onClick={() => {
                    navigator.clipboard.writeText(channel.key);
                    toast.success(t('channelInfo.keyCopied'));
                  }}
                  title={t('channelInfo.clickToCopy')}
                >
                  {channel.key.toLowerCase()}
                </span>
              )}
              <div className="flex items-center gap-2 mt-1.5">
                <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                  {channel.is_hashtag ? t('channelInfo.hashtag') : t('channelInfo.privateKey')}
                </span>
                {channel.on_radio && (
                  <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                    {t('channelInfo.onRadio')}
                  </span>
                )}
              </div>
            </div>

            {/* Favorite toggle */}
            <div className="px-5 py-3 border-b border-border">
              <button
                type="button"
                className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                onClick={() => onToggleFavorite('channel', channel.key)}
              >
                {channel.favorite ? (
                  <>
                    <Star className="h-4.5 w-4.5 fill-current text-favorite" aria-hidden="true" />
                    <span>{t('channelInfo.removeFavorite')}</span>
                  </>
                ) : (
                  <>
                    <Star className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                    <span>{t('channelInfo.addFavorite')}</span>
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

            {/* Message Activity */}
            {detail && detail.message_counts.all_time > 0 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>{t('channelInfo.activity')}</SectionLabel>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <InfoItem
                    label={t('channelInfo.lastHour')}
                    value={formatNumber(detail.message_counts.last_1h)}
                  />
                  <InfoItem
                    label={t('channelInfo.last24h')}
                    value={formatNumber(detail.message_counts.last_24h)}
                  />
                  <InfoItem
                    label={t('channelInfo.last48h')}
                    value={formatNumber(detail.message_counts.last_48h)}
                  />
                  <InfoItem
                    label={t('channelInfo.last7d')}
                    value={formatNumber(detail.message_counts.last_7d)}
                  />
                  <InfoItem
                    label={t('channelInfo.allTime')}
                    value={formatNumber(detail.message_counts.all_time)}
                  />
                  <InfoItem
                    label={t('channelInfo.uniqueSenders')}
                    value={formatNumber(detail.unique_sender_count)}
                  />
                </div>
              </div>
            )}

            {/* First Message */}
            {detail && detail.first_message_at && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>{t('channelInfo.firstMessage')}</SectionLabel>
                <p className="text-sm font-medium">{formatTime(detail.first_message_at)}</p>
              </div>
            )}

            {/* Hop Byte Widths (24h) */}
            {detail && detail.path_hash_width_24h.total_packets > 0 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>{t('channelInfo.hopWidths')}</SectionLabel>
                <HopWidthChart stats={detail.path_hash_width_24h} ready={chartReady} />
              </div>
            )}

            {/* Top Senders 24h */}
            {detail && detail.top_senders_24h.length > 0 && (
              <div className="px-5 py-3">
                <SectionLabel>{t('channelInfo.topSenders')}</SectionLabel>
                <div className="space-y-1">
                  {detail.top_senders_24h.map((sender, idx) => (
                    <div
                      key={sender.sender_key ?? idx}
                      className="flex justify-between items-center text-sm"
                    >
                      <span className="truncate">{sender.sender_name}</span>
                      <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
                        {t('channelInfo.msgCount', { count: sender.message_count })}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            {t('channelInfo.notFound')}
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

function InfoItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-muted-foreground text-xs">{label}</span>
      <p className="font-medium text-sm leading-tight">{value}</p>
    </div>
  );
}

const HOP_WIDTH_SEGMENTS = [
  { key: 'single_byte', labelKey: 'channelInfo.hop1', color: '#22c55e' },
  { key: 'double_byte', labelKey: 'channelInfo.hop2', color: '#0ea5e9' },
  { key: 'triple_byte', labelKey: 'channelInfo.hop3', color: '#8b5cf6' },
] as const;

const TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: 'hsl(var(--popover))',
    border: '1px solid hsl(var(--border))',
    borderRadius: '6px',
    fontSize: '11px',
    color: 'hsl(var(--popover-foreground))',
  },
} as const;

function HopWidthChart({ stats, ready }: { stats: PathHashWidthStats; ready: boolean }) {
  const { t } = useTranslation();
  const data = useMemo(
    () =>
      HOP_WIDTH_SEGMENTS.map(({ key, labelKey, color }) => ({
        name: t(labelKey),
        value: stats[key] as number,
        color,
      })).filter((d) => d.value > 0),
    [stats, t]
  );

  return (
    <div className="flex items-center gap-3">
      <div className="flex-shrink-0" style={{ width: 90, height: 90 }}>
        {/* Reserve the box while the pane animates in (see #317). */}
        {ready && (
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                cx="50%"
                cy="50%"
                innerRadius={22}
                outerRadius={40}
                strokeWidth={1.5}
                stroke="hsl(var(--background))"
              >
                {data.map((d) => (
                  <Cell key={d.name} fill={d.color} />
                ))}
              </Pie>
              <RechartsTooltip
                {...TOOLTIP_STYLE}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                formatter={(value: any, name: any) => {
                  const v = typeof value === 'number' ? value : Number(value);
                  return [t('channelInfo.pktCount', { count: v }), name];
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="flex-1 space-y-1">
        {data.map((d) => (
          <div key={d.name} className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ backgroundColor: d.color }}
            />
            <span className="text-[0.6875rem] text-muted-foreground flex-1">{d.name}</span>
            <span className="text-[0.6875rem] font-medium tabular-nums">
              {formatNumber(d.value)}
            </span>
          </div>
        ))}
        <p className="text-[0.625rem] text-muted-foreground pt-0.5">
          {t('channelInfo.totalPackets', { count: stats.total_packets })}
        </p>
      </div>
    </div>
  );
}
