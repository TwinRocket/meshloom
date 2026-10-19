import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus } from 'lucide-react';

import { api, formatApiError } from '../../../api';
import type { NotificationChannel } from '../../../notifications/types';
import type { AppSettings, AppSettingsUpdate } from '../../../types';
import { Button } from '../../ui/button';
import { toast } from '../../ui/sonner';
import { SettingsGroupHeader } from '../settingsPrimitives';
import { AddChannelDialog } from './AddChannelDialog';
import { ChannelEditDialog } from './ChannelEditDialog';
import { ChannelList } from './ChannelList';
import { entryOfChannel } from './notificationsShared';
import { PushDevicesBlock } from './PushDevicesBlock';
import type { NotificationChannelsState } from './useNotificationChannels';

/** Settings > Notifications, channels: cards, the add dialog and the edit dialog. */
export function NotificationChannelsPanel({
  state,
  appSettings,
  onSaveAppSettings,
}: {
  state: NotificationChannelsState;
  appSettings: AppSettings | null;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { channels, types, rules, events, loading, error, reload } = state;
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<NotificationChannel | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const run = async (
    channel: NotificationChannel,
    action: () => Promise<unknown>,
    failure: string,
    success?: string
  ) => {
    setBusyId(channel.id);
    try {
      await action();
      if (success) toast.success(success);
    } catch (err) {
      // `legacy_managed` (409) explains that the 4.18.x setting still decides.
      toast.error(failure, { description: formatApiError(err, t) });
    } finally {
      setBusyId(null);
      await reload();
    }
  };

  const editEntry = editing ? entryOfChannel(types, editing) : undefined;

  return (
    <div className="space-y-3" data-testid="notification-channels">
      <SettingsGroupHeader
        title={t('settings.notifications.channels.title')}
        storedOn="server"
        instant
      />
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.notifications.channels.help')}
      </p>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {formatApiError(error, t)}
        </p>
      ) : loading ? (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.channels.loading')}
        </p>
      ) : (
        <ChannelList
          channels={channels}
          busyId={busyId}
          pushOpen={
            appSettings?.push_private_endpoints_grandfathered === true ||
            appSettings?.push_private_endpoints_review === true
          }
          pushBlock={
            <PushDevicesBlock appSettings={appSettings} onSaveAppSettings={onSaveAppSettings} />
          }
          onToggle={(channel, enabled) =>
            void run(
              channel,
              () => api.updateNotificationChannel(channel.id, { enabled }),
              t('settings.notifications.channels.toggleFailed', { name: channel.name })
            )
          }
          onTest={(channel) =>
            void run(
              channel,
              () => api.testNotificationChannel(channel.id),
              t('settings.notifications.channels.testFailed', { name: channel.name }),
              t('settings.notifications.channels.testSent')
            )
          }
          onEdit={setEditing}
          onDelete={(channel) =>
            void run(
              channel,
              () => api.deleteNotificationChannel(channel.id),
              t('settings.notifications.channels.deleteFailed', { name: channel.name }),
              t('settings.notifications.channels.deleted')
            )
          }
        />
      )}
      <Button
        variant="outline"
        size="sm"
        disabled={loading || !!error}
        onClick={() => setAdding(true)}
      >
        <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
        {t('settings.notifications.channels.add')}
      </Button>

      {adding && (
        <AddChannelDialog
          types={types}
          rules={rules}
          events={events}
          onClose={() => setAdding(false)}
          onCreated={() => void reload()}
        />
      )}
      {editing && editEntry && (
        <ChannelEditDialog
          channel={editing}
          entry={editEntry}
          onClose={() => setEditing(null)}
          onSaved={() => void reload()}
        />
      )}
    </div>
  );
}
