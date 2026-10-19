import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { toast } from '../ui/sonner';
import { formatApiError } from '../../api';
import { usePush } from '../../contexts/PushSubscriptionContext';
import {
  resolveNotificationMedia,
  type AppSettings,
  type AppSettingsUpdate,
  type Channel,
  type Contact,
  type NotificationLanguage,
  type NotificationMediaChannel,
  type PushDefaults,
} from '../../types';
import { conversationOverrideFlags } from '../../utils/pushPolicy';
import { getContactDisplayName } from '../../utils/pubkey';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { SettingsGroupHeader } from './settingsPrimitives';
import { NotificationChannelsPanel } from './notifications/NotificationChannelsPanel';
import { useNotificationChannels } from './notifications/useNotificationChannels';

const DEFAULT_KEYS: Array<{
  key: keyof PushDefaults;
  label: string;
  help: string;
}> = [
  {
    key: 'new_contact',
    label: 'settings.notifications.newContact',
    help: 'settings.notifications.newContactHelp',
  },
  {
    key: 'new_dm',
    label: 'settings.notifications.newDm',
    help: 'settings.notifications.newDmHelp',
  },
  {
    key: 'advert_repeater',
    label: 'settings.notifications.advertRepeater',
    help: 'settings.notifications.advertRepeaterHelp',
  },
  {
    key: 'advert_companion',
    label: 'settings.notifications.advertCompanion',
    help: 'settings.notifications.advertCompanionHelp',
  },
  {
    key: 'advert_sensor',
    label: 'settings.notifications.advertSensor',
    help: 'settings.notifications.advertSensorHelp',
  },
  {
    key: 'channel_found',
    label: 'settings.notifications.channelFound',
    help: 'settings.notifications.channelFoundHelp',
  },
  {
    key: 'oss_update',
    label: 'settings.notifications.ossUpdate',
    help: 'settings.notifications.ossUpdateHelp',
  },
  {
    key: 'radio_offline',
    label: 'settings.notifications.radioOffline',
    help: 'settings.notifications.radioOfflineHelp',
  },
  {
    key: 'radio_online',
    label: 'settings.notifications.radioOnline',
    help: 'settings.notifications.radioOnlineHelp',
  },
  {
    key: 'app_started',
    label: 'settings.notifications.appStarted',
    help: 'settings.notifications.appStartedHelp',
  },
];

const DEFAULT_RADIO_OFFLINE_GRACE_S = 60;
const MAX_RADIO_OFFLINE_GRACE_S = 3600;

const MEDIA_COLUMNS: Array<{
  channel: NotificationMediaChannel;
  label: string;
}> = [
  { channel: 'push', label: 'settings.notifications.mediaPush' },
  { channel: 'email', label: 'settings.notifications.mediaEmail' },
  { channel: 'webhook', label: 'settings.notifications.mediaWebhook' },
  { channel: 'apprise', label: 'settings.notifications.mediaApprise' },
];

const SELECT_CLASS =
  'h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

function resolveConversationName(
  stateKey: string,
  contacts: Contact[],
  channels: Channel[]
): string {
  if (stateKey.startsWith('contact-')) {
    const pubkey = stateKey.slice('contact-'.length);
    const contact = contacts.find((c) => c.public_key === pubkey);
    return contact ? getContactDisplayName(contact.name, contact.public_key) : pubkey.slice(0, 12);
  }
  if (stateKey.startsWith('channel-')) {
    const key = stateKey.slice('channel-'.length);
    const channel = channels.find((c) => c.key === key);
    if (channel?.name) return channel.name.startsWith('#') ? channel.name : `#${channel.name}`;
    return `#${key.slice(0, 12)}`;
  }
  return stateKey;
}

export function SettingsNotificationsSection({
  appSettings = null,
  onSaveAppSettings,
  contacts = [],
  channels = [],
  className,
}: {
  appSettings?: AppSettings | null;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
  contacts?: Contact[];
  channels?: Channel[];
  className?: string;
}) {
  const { t } = useTranslation();
  const { preferences, overrideEntries, setConversationOverride, patchPreferences } = usePush();
  const notificationChannels = useNotificationChannels();

  const storedGrace = appSettings?.radio_offline_grace_s ?? DEFAULT_RADIO_OFFLINE_GRACE_S;
  const [graceDraft, setGraceDraft] = useState(String(storedGrace));
  const [prevStoredGrace, setPrevStoredGrace] = useState(storedGrace);
  if (storedGrace !== prevStoredGrace) {
    setPrevStoredGrace(storedGrace);
    setGraceDraft(String(storedGrace));
  }

  const commitGrace = () => {
    const parsed = Number(graceDraft.trim());
    if (
      graceDraft.trim() === '' ||
      !Number.isInteger(parsed) ||
      parsed < 0 ||
      parsed > MAX_RADIO_OFFLINE_GRACE_S
    ) {
      setGraceDraft(String(storedGrace));
      return;
    }
    if (parsed === storedGrace || !onSaveAppSettings) return;
    void onSaveAppSettings({ radio_offline_grace_s: parsed }).catch((err: unknown) => {
      setGraceDraft(String(storedGrace));
      toast.error(t('settings.notifications.radioOfflineGraceSaveFailed'), {
        description: formatApiError(err, t),
      });
    });
  };

  const patchMedia = (
    key: keyof PushDefaults,
    channel: NotificationMediaChannel,
    checked: boolean
  ) => {
    void patchPreferences({ defaults: { [key]: { [channel]: checked } } });
  };

  // A legacy medium takes part in the matrix once its channel exists and is switched on.
  const mediaReady = (channel: NotificationMediaChannel) =>
    channel === 'push' ||
    notificationChannels.channels.some((c) => c.id === `legacy:${channel}` && c.enabled);

  return (
    <div className={className}>
      <NotificationChannelsPanel
        state={notificationChannels}
        appSettings={appSettings}
        onSaveAppSettings={onSaveAppSettings}
      />

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.notifications.defaults')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.defaultsHelp')}
        </p>
        <div className="overflow-x-auto rounded-md border border-border/60">
          <table className="w-full min-w-md text-sm">
            <thead>
              <tr className="border-b border-border/60 text-left">
                <th className="px-3 py-2 font-medium">
                  {t('settings.notifications.defaultsType')}
                </th>
                {MEDIA_COLUMNS.map(({ channel, label }) => (
                  <th key={channel} className="px-3 py-2 text-center font-medium">
                    {t(label)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {DEFAULT_KEYS.map(({ key, label, help }) => {
                const media = resolveNotificationMedia(preferences?.defaults[key]);
                return (
                  <tr key={key} className="border-b border-border/40 last:border-0">
                    <td className="px-3 py-3 align-top">
                      <div className="space-y-1">
                        <span className="font-medium">{t(label)}</span>
                        <p className="text-[0.8125rem] text-muted-foreground">{t(help)}</p>
                      </div>
                    </td>
                    {MEDIA_COLUMNS.map(({ channel, label: mediaLabel }) => {
                      const ready = mediaReady(channel);
                      const id = `notify-${key}-${channel}`;
                      return (
                        <td key={channel} className="px-3 py-3 text-center align-top">
                          <Checkbox
                            id={id}
                            checked={media[channel]}
                            disabled={!preferences || !ready}
                            onCheckedChange={(checked) => {
                              patchMedia(key, channel, checked === true);
                            }}
                            aria-label={`${t(label)} — ${t(mediaLabel)}`}
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="space-y-1">
          <Label htmlFor="notification-language">
            {t('settings.notifications.language.label')}
          </Label>
          <select
            id="notification-language"
            value={appSettings?.notification_language ?? 'auto'}
            disabled={!onSaveAppSettings || !appSettings}
            onChange={(event) => {
              void onSaveAppSettings?.({
                notification_language: event.target.value as NotificationLanguage,
              }).catch((err: unknown) => {
                toast.error(t('settings.notifications.language.saveFailed'), {
                  description: formatApiError(err, t),
                });
              });
            }}
            className={SELECT_CLASS}
          >
            <option value="auto">{t('settings.notifications.language.auto')}</option>
            <option value="fr">{t('settings.notifications.language.fr')}</option>
            <option value="en">{t('settings.notifications.language.en')}</option>
          </select>
          <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.language.help')}
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="radio-offline-grace">
            {t('settings.notifications.radioOfflineGrace')}
          </Label>
          <div className="flex items-center gap-2">
            <Input
              id="radio-offline-grace"
              type="number"
              inputMode="numeric"
              min={0}
              max={MAX_RADIO_OFFLINE_GRACE_S}
              step={1}
              className="w-24"
              value={graceDraft}
              disabled={!onSaveAppSettings || !appSettings}
              onChange={(e) => setGraceDraft(e.target.value)}
              onBlur={commitGrace}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur();
              }}
            />
            <span className="text-[0.8125rem] text-muted-foreground">
              {t('settings.notifications.radioOfflineGraceUnit')}
            </span>
          </div>
          <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.radioOfflineGraceHelp')}
          </p>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.notifications.exceptions')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.exceptionsHelp')}
        </p>
        {overrideEntries.length === 0 ? (
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.exceptionsEmpty')}
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {overrideEntries.map((key) => {
              const name = resolveConversationName(key, contacts, channels);
              const flags = conversationOverrideFlags(preferences?.overrides[key]);
              const mediaLabels = {
                push: t('settings.notifications.mediaPush'),
                email: t('settings.notifications.mediaEmail'),
                webhook: t('settings.notifications.mediaWebhook'),
              } as const;
              const summary = (['push', 'email', 'webhook'] as const)
                .filter((channel) => flags[channel] != null)
                .map(
                  (channel) =>
                    `${mediaLabels[channel]} ${
                      flags[channel]
                        ? t('settings.notifications.overrideOn')
                        : t('settings.notifications.overrideOff')
                    }`
                )
                .join(' · ');
              return (
                <span
                  key={key}
                  className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-sm"
                >
                  <span>{name}</span>
                  <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                    {summary || t('settings.notifications.overrideOff')}
                  </span>
                  <button
                    type="button"
                    onClick={() => void setConversationOverride(key, null)}
                    className="rounded-full p-0.5 hover:bg-accent transition-colors"
                    title={t('settings.remove')}
                    aria-label={t('settings.notifications.removeOverride', { name })}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </span>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
