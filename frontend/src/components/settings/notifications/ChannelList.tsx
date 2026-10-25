import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight } from 'lucide-react';

import { BUILTIN_PUSH_ID } from '../../../notifications/ids';
import type { NotificationChannel } from '../../../notifications/types';
import { Button } from '../../ui/button';
import { iconFor, isLegacyChannel, STATUS_DOT_CLASS } from './notificationsShared';

export function ChannelList({
  channels,
  busyId,
  pushBlock,
  pushOpen = false,
  legacyBlock,
  onToggle,
  onTest,
  onEdit,
  onDelete,
}: {
  channels: NotificationChannel[];
  busyId: string | null;
  /** The browser/devices block, shown inside the `builtin:push` card. */
  pushBlock: ReactNode;
  /** Start with the Web Push card open (a notice about private endpoints is waiting there). */
  pushOpen?: boolean;
  /** The 4.18.x destination form of a `legacy:*` channel (its Edit opens it in the card). */
  legacyBlock: (medium: 'email' | 'webhook' | 'apprise') => ReactNode;
  onToggle: (channel: NotificationChannel, enabled: boolean) => void;
  onTest: (channel: NotificationChannel) => void;
  onEdit: (channel: NotificationChannel) => void;
  onDelete: (channel: NotificationChannel) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string | null>(pushOpen ? BUILTIN_PUSH_ID : null);
  const [confirming, setConfirming] = useState<string | null>(null);

  if (channels.length === 0) {
    return (
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.notifications.channels.empty')}
      </p>
    );
  }

  return (
    <ul className="space-y-2" data-testid="channel-list">
      {channels.map((channel) => {
        const Icon = iconFor(channel.type);
        const builtin = channel.id === BUILTIN_PUSH_ID;
        const legacy = isLegacyChannel(channel.id);
        const status = channel.status.status;
        const busy = busyId === channel.id;
        const expanded = open === channel.id;
        const typeName = t(`settings.notifications.types.${channel.preset ?? channel.type}.name`, {
          defaultValue: channel.preset ?? channel.type,
        });
        return (
          <li
            key={channel.id}
            className="rounded-md border border-border/60 p-3"
            data-testid={`channel-card-${channel.id}`}
          >
            <div className="flex items-start gap-3">
              <Icon className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="break-words text-sm font-medium">
                    {builtin ? t('settings.notifications.channels.builtinPushName') : channel.name}
                  </span>
                  <span className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                    {typeName}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${STATUS_DOT_CLASS[status] ?? STATUS_DOT_CLASS.disconnected}`}
                    aria-hidden="true"
                  />
                  <span data-testid={`channel-status-${channel.id}`}>
                    {t(`settings.notifications.channels.status.${status}`, {
                      defaultValue: status,
                    })}
                  </span>
                </div>
                {channel.status.last_error && (
                  <p className="break-words text-xs text-destructive">
                    {t(`errors.${channel.status.last_error}`, {
                      defaultValue: t('settings.notifications.channels.lastError', {
                        code: channel.status.last_error,
                      }),
                    })}
                  </p>
                )}
                {builtin && !window.isSecureContext && (
                  <p className="text-xs text-muted-foreground" data-testid="push-unavailable-http">
                    {t('settings.notifications.channels.pushUnavailableHttp')}
                  </p>
                )}
              </div>
              {!builtin && (
                <button
                  type="button"
                  role="switch"
                  aria-checked={channel.enabled}
                  aria-label={t('settings.notifications.channels.enabledFor', {
                    name: channel.name,
                  })}
                  disabled={busy}
                  onClick={() => onToggle(channel, !channel.enabled)}
                  className={`relative h-6 w-11 shrink-0 rounded-full border border-border transition-colors disabled:opacity-50 ${
                    channel.enabled ? 'bg-primary' : 'bg-muted'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-4.5 w-4.5 rounded-full bg-background transition-all ${
                      channel.enabled ? 'left-5.5' : 'left-0.5'
                    }`}
                  />
                </button>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {builtin ? (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : channel.id)}
                >
                  {expanded ? (
                    <ChevronDown className="mr-1 h-4 w-4" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="mr-1 h-4 w-4" aria-hidden="true" />
                  )}
                  {t('settings.notifications.channels.devices')}
                </Button>
              ) : (
                <>
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => onTest(channel)}>
                    {t('settings.notifications.test')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    aria-expanded={legacy ? expanded : undefined}
                    onClick={() =>
                      legacy ? setOpen(expanded ? null : channel.id) : onEdit(channel)
                    }
                  >
                    {t('settings.notifications.channels.edit')}
                  </Button>
                  {channel.deletable &&
                    !legacy &&
                    (confirming === channel.id ? (
                      <>
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={busy}
                          onClick={() => {
                            setConfirming(null);
                            onDelete(channel);
                          }}
                        >
                          {t('settings.notifications.channels.deleteConfirm')}
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setConfirming(null)}>
                          {t('settings.notifications.channels.cancel')}
                        </Button>
                      </>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        disabled={busy}
                        onClick={() => setConfirming(channel.id)}
                      >
                        {t('settings.notifications.channels.delete')}
                      </Button>
                    ))}
                </>
              )}
            </div>
            {builtin && expanded && <div className="mt-3">{pushBlock}</div>}
            {legacy && expanded && (
              <div className="mt-3 space-y-2">
                <p className="text-[0.8125rem] text-muted-foreground">
                  {t('settings.notifications.channels.legacyHelp')}
                </p>
                {legacyBlock(channel.id.slice('legacy:'.length) as 'email' | 'webhook' | 'apprise')}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
