import { useState, useEffect } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
  Cell,
} from 'recharts';
import { useTranslation } from 'react-i18next';
import { Separator } from '../ui/separator';
import { api } from '../../api';
import type { RegionScopeStats, StatisticsResponse } from '../../types';
import { formatNumber } from '../../utils/formatNumber';

function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`;
}

/**
 * Regional flood-scope adoption. Deliberately shows fractions rather than bare
 * percentages: with adoption this sparse, "3 of 117" communicates the sample
 * size that "2.6%" hides.
 */
function RegionScopeStatsPanel({ stats }: { stats: RegionScopeStats }) {
  const { t } = useTranslation();
  // Corrupt RF captures land in the packet table with random headers, some of
  // which claim to be region-scoped. At or below the measured floor there is
  // nothing to report but noise, so withhold the percentage and say so.
  const floor = stats.false_positive_floor;
  const withinNoise = stats.scoped_messages <= floor;
  // Real-world scoping is currently rare enough that the share rounds to "0.0%",
  // which reads as a broken widget. Below that resolution the fraction alone is
  // the honest presentation.
  const showTrafficPct = stats.total_messages > 0 && !withinNoise && stats.scoped_pct >= 0.05;

  return (
    <div>
      <h3 className="text-base font-semibold tracking-tight mb-2">
        {t('settings.stats.regionScope')}
      </h3>
      <p className="text-[0.8125rem] text-muted-foreground mb-3">
        {t('settings.stats.regionScopeHelp')}
      </p>
      <div className="space-y-2">
        <div className="flex justify-between items-center gap-4">
          <span className="text-sm text-muted-foreground">
            {t('settings.stats.scopedMessages')}
          </span>
          <span className="font-medium text-right">
            {t('settings.stats.ofTotal', {
              scoped: formatNumber(stats.scoped_messages),
              total: formatNumber(stats.total_messages),
            })}
            {showTrafficPct && (
              <span className="text-muted-foreground"> ({formatPercent(stats.scoped_pct)})</span>
            )}
          </span>
        </div>
        <div className="flex justify-between items-center gap-4">
          <span className="text-sm text-muted-foreground">{t('settings.stats.sendersUsing')}</span>
          <span className="font-medium text-right">
            {t('settings.stats.ofTotal', {
              scoped: formatNumber(stats.scoped_senders),
              total: formatNumber(stats.total_senders),
            })}
            {stats.total_senders > 0 && (
              <span className="text-muted-foreground">
                {' '}
                ({formatPercent(stats.scoped_senders_pct)})
              </span>
            )}
          </span>
        </div>
      </div>
      {floor > 0 && stats.scoped_messages > 0 && (
        <p className="text-[0.8125rem] text-muted-foreground mt-2">
          {withinNoise
            ? t('settings.stats.floorNoise', { floor: floor.toFixed(0) })
            : t('settings.stats.floorIncludes', { floor: floor.toFixed(0) })}
          {t('settings.stats.senderUnaffected')}
        </p>
      )}
      {stats.total_messages === 0 && (
        <p className="text-sm text-muted-foreground mt-2">
          {t('settings.stats.noChannelMessages')}
        </p>
      )}
    </div>
  );
}

const CHANNEL_BAR_COLORS = ['#0ea5e9', '#10b981', '#f59e0b', '#f43f5e', '#8b5cf6'];

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

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function formatDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  return (
    d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  );
}

function PacketsPerHourChart({ buckets }: { buckets: { timestamp: number; count: number }[] }) {
  const { t } = useTranslation();
  // Fill gaps so hours with zero packets still appear on the chart
  const filled: { timestamp: number; count: number }[] = [];
  if (buckets.length > 0) {
    const first = buckets[0].timestamp;
    const last = buckets[buckets.length - 1].timestamp;
    const byTs = new Map(buckets.map((b) => [b.timestamp, b.count]));
    for (let ts = first; ts <= last; ts += 3600) {
      filled.push({ timestamp: ts, count: byTs.get(ts) ?? 0 });
    }
  }

  const data = filled.map((b, i) => ({
    idx: i,
    label: formatDateTime(b.timestamp),
    count: b.count,
  }));

  // Show ~6 evenly-spaced tick labels
  const tickCount = Math.min(6, data.length);
  const tickIndices: number[] = [];
  if (data.length > 1) {
    for (let i = 0; i < tickCount; i++) {
      tickIndices.push(Math.round((i / (tickCount - 1)) * (data.length - 1)));
    }
  }

  return (
    <ResponsiveContainer width="100%" height={140}>
      <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
        <XAxis
          dataKey="idx"
          type="number"
          domain={[0, data.length - 1]}
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          ticks={tickIndices}
          tickFormatter={(idx) => data[idx]?.label ?? ''}
        />
        <YAxis
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
        />
        <RechartsTooltip
          {...TOOLTIP_STYLE}
          cursor={{
            stroke: 'hsl(var(--muted-foreground))',
            strokeWidth: 1,
            strokeDasharray: '3 3',
          }}
          labelFormatter={(idx) => data[Number(idx)]?.label ?? ''}
          formatter={(value) => [
            t('settings.stats.tooltipPacketsValue', {
              count: Number(value),
            }),
            t('settings.stats.tooltipCount'),
          ]}
        />
        <Area
          type="monotone"
          dataKey="count"
          stroke="#0ea5e9"
          fill="#0ea5e9"
          fillOpacity={0.15}
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 4, fill: '#0ea5e9', strokeWidth: 2, stroke: 'hsl(var(--popover))' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function NoiseFloorChart({
  samples,
}: {
  samples: { timestamp: number; noise_floor_dbm: number }[];
}) {
  const { t } = useTranslation();
  const data = samples.map((s, i) => ({
    idx: i,
    time: formatTime(s.timestamp),
    noise_floor: s.noise_floor_dbm,
  }));

  const tickCount = Math.min(6, samples.length);
  const tickIndices: number[] = [];
  if (samples.length > 1) {
    for (let i = 0; i < tickCount; i++) {
      tickIndices.push(Math.round((i / (tickCount - 1)) * (samples.length - 1)));
    }
  }

  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
        <XAxis
          dataKey="idx"
          type="number"
          domain={[0, samples.length - 1]}
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          ticks={tickIndices}
          tickFormatter={(idx) => data[idx]?.time ?? ''}
        />
        <YAxis
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          domain={['dataMin - 5', 'dataMax + 5']}
          tickFormatter={(v) => `${v}`}
        />
        <RechartsTooltip
          {...TOOLTIP_STYLE}
          cursor={{
            stroke: 'hsl(var(--muted-foreground))',
            strokeWidth: 1,
            strokeDasharray: '3 3',
          }}
          labelFormatter={(idx) => data[Number(idx)]?.time ?? ''}
          formatter={(value) => [`${value} dBm`, t('settings.stats.tooltipNoise')]}
        />
        <Area
          type="linear"
          dataKey="noise_floor"
          stroke="#8b5cf6"
          fill="#8b5cf6"
          fillOpacity={0.15}
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 4, fill: '#8b5cf6', strokeWidth: 2, stroke: 'hsl(var(--popover))' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SettingsStatisticsSection({ className }: { className?: string }) {
  const { t } = useTranslation();
  const [stats, setStats] = useState<StatisticsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setStatsLoading(true);
    setStatsError(false);
    api.getStatistics().then(
      (data) => {
        if (!cancelled) {
          setStats(data);
          setStatsLoading(false);
        }
      },
      () => {
        if (!cancelled) {
          setStatsError(true);
          setStatsLoading(false);
        }
      }
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className={className}>
      {statsLoading && !stats ? (
        <div className="py-8 text-center text-muted-foreground">{t('settings.stats.loading')}</div>
      ) : stats ? (
        <div className="space-y-6">
          {/* Network */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">
              {t('settings.stats.network')}
            </h3>
            <div className="grid grid-cols-3 gap-3">
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.contact_count}</div>
                <div className="text-xs text-muted-foreground">{t('settings.stats.contacts')}</div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.repeater_count}</div>
                <div className="text-xs text-muted-foreground">{t('settings.stats.repeaters')}</div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.channel_count}</div>
                <div className="text-xs text-muted-foreground">{t('settings.stats.channels')}</div>
              </div>
            </div>
          </div>

          <Separator />

          {/* Messages */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">
              {t('settings.stats.messages')}
            </h3>
            <div className="grid grid-cols-3 gap-3">
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.total_dms}</div>
                <div className="text-xs text-muted-foreground">{t('settings.stats.dms')}</div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.total_channel_messages}</div>
                <div className="text-xs text-muted-foreground">
                  {t('settings.stats.channelMessages')}
                </div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.total_outgoing}</div>
                <div className="text-xs text-muted-foreground">{t('settings.stats.outgoing')}</div>
              </div>
            </div>
          </div>

          <Separator />

          {/* Activity */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">
              {t('settings.stats.activity')}
            </h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="text-left font-normal pb-1"></th>
                  <th className="text-right font-normal pb-1">1h</th>
                  <th className="text-right font-normal pb-1">24h</th>
                  <th className="text-right font-normal pb-1">7d</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="py-1">{t('settings.stats.contactsHeard')}</td>
                  <td className="text-right py-1">{stats.contacts_heard.last_hour}</td>
                  <td className="text-right py-1">{stats.contacts_heard.last_24_hours}</td>
                  <td className="text-right py-1">{stats.contacts_heard.last_week}</td>
                </tr>
                <tr>
                  <td className="py-1">{t('settings.stats.repeatersHeard')}</td>
                  <td className="text-right py-1">{stats.repeaters_heard.last_hour}</td>
                  <td className="text-right py-1">{stats.repeaters_heard.last_24_hours}</td>
                  <td className="text-right py-1">{stats.repeaters_heard.last_week}</td>
                </tr>
                <tr>
                  <td className="py-1">{t('settings.stats.channelsActive')}</td>
                  <td className="text-right py-1">{stats.known_channels_active.last_hour}</td>
                  <td className="text-right py-1">{stats.known_channels_active.last_24_hours}</td>
                  <td className="text-right py-1">{stats.known_channels_active.last_week}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <Separator />

          {/* Packets */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">
              {t('settings.stats.packets')}
            </h3>
            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">
                  {t('settings.stats.totalStored')}
                </span>
                <span className="font-medium">{stats.total_packets}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-success">{t('settings.stats.decrypted')}</span>
                <span className="font-medium text-success">{stats.decrypted_packets}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-warning">{t('settings.stats.undecrypted')}</span>
                <span className="font-medium text-warning">{stats.undecrypted_packets}</span>
              </div>
            </div>
          </div>

          {/* Packets per Hour (72h) */}
          {stats.packets_per_hour_72h?.length > 0 && (
            <>
              <Separator />
              <div>
                <h3 className="text-base font-semibold tracking-tight mb-2">
                  {t('settings.stats.packetsPerHour')}
                </h3>
                <PacketsPerHourChart buckets={stats.packets_per_hour_72h} />
              </div>
            </>
          )}

          <Separator />

          {/* Path Hash Width */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">
              {t('settings.stats.pathHashWidth')}
            </h3>
            <div className="mb-2 text-xs text-muted-foreground">
              {t('settings.stats.pathHashHelp', {
                count: stats.path_hash_width_24h.total_packets,
              })}
            </div>
            {stats.path_hash_width_24h.total_packets > 0 ? (
              <ResponsiveContainer width="100%" height={120}>
                <BarChart
                  data={[
                    {
                      name: t('settings.stats.byte1'),
                      count: stats.path_hash_width_24h.single_byte,
                      pct: stats.path_hash_width_24h.single_byte_pct,
                    },
                    {
                      name: t('settings.stats.byte2'),
                      count: stats.path_hash_width_24h.double_byte,
                      pct: stats.path_hash_width_24h.double_byte_pct,
                    },
                    {
                      name: t('settings.stats.byte3'),
                      count: stats.path_hash_width_24h.triple_byte,
                      pct: stats.path_hash_width_24h.triple_byte_pct,
                    },
                  ]}
                  margin={{ top: 4, right: 4, bottom: 0, left: -16 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="hsl(var(--border))"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="name"
                    tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                  />
                  <RechartsTooltip
                    {...TOOLTIP_STYLE}
                    cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    formatter={(value: any, _: any, props: any) => [
                      `${formatNumber(Number(value))} (${formatPercent(props.payload.pct)})`,
                      t('settings.stats.tooltipPackets'),
                    ]}
                  />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={40}>
                    <Cell fill="#0ea5e9" />
                    <Cell fill="#10b981" />
                    <Cell fill="#f59e0b" />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-sm text-muted-foreground">{t('settings.stats.noPathData')}</p>
            )}
          </div>

          <Separator />

          {/* Region Scope */}
          <RegionScopeStatsPanel stats={stats.region_scope_24h} />

          {/* Busiest Channels */}
          {stats.busiest_channels_24h.length > 0 && (
            <>
              <Separator />
              <div>
                <h3 className="text-base font-semibold tracking-tight mb-2">
                  {t('settings.stats.busiest')}
                </h3>
                <ResponsiveContainer
                  width="100%"
                  height={stats.busiest_channels_24h.length * 28 + 8}
                >
                  <BarChart
                    data={stats.busiest_channels_24h.map((ch) => ({
                      name: ch.channel_name,
                      messages: ch.message_count,
                    }))}
                    layout="vertical"
                    margin={{ top: 0, right: 4, bottom: 0, left: 0 }}
                    barCategoryGap="20%"
                  >
                    <XAxis type="number" hide />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                      tickLine={false}
                      axisLine={false}
                      width={100}
                    />
                    <RechartsTooltip
                      {...TOOLTIP_STYLE}
                      cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
                      formatter={(value) => [
                        t('settings.stats.tooltipMessages', {
                          count: Number(value),
                        }),
                        null,
                      ]}
                    />
                    <Bar dataKey="messages" radius={[0, 4, 4, 0]} maxBarSize={16}>
                      {stats.busiest_channels_24h.map((_, i) => (
                        <Cell key={i} fill={CHANNEL_BAR_COLORS[i % CHANNEL_BAR_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          )}

          {/* Noise Floor */}
          {stats.noise_floor_24h && (
            <>
              <Separator />
              <div>
                <h3 className="text-base font-semibold tracking-tight mb-2">
                  {t('settings.stats.noiseFloor')}
                </h3>
                {stats.noise_floor_24h.latest_noise_floor_dbm != null && (
                  <div className="mb-2 text-xs text-muted-foreground">
                    {t('settings.stats.latestReading', {
                      dbm: stats.noise_floor_24h.latest_noise_floor_dbm,
                    })}
                    {stats.noise_floor_24h.latest_timestamp != null &&
                      t('settings.stats.latestAt', {
                        time: new Date(
                          stats.noise_floor_24h.latest_timestamp * 1000
                        ).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        }),
                      })}
                  </div>
                )}
                {stats.noise_floor_24h.samples.length > 1 ? (
                  <NoiseFloorChart samples={stats.noise_floor_24h.samples} />
                ) : stats.noise_floor_24h.samples.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t('settings.stats.noNoiseSamples')}
                  </p>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {t('settings.stats.oneNoiseSample', {
                      dbm: stats.noise_floor_24h.samples[0].noise_floor_dbm,
                    })}
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      ) : statsError ? (
        <div className="py-8 text-center text-muted-foreground">{t('settings.stats.failed')}</div>
      ) : null}
    </div>
  );
}
