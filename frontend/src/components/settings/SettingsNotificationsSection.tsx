import { useTranslation } from 'react-i18next';
import { toast } from '../ui/sonner';
import { formatApiError } from '../../api';
import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  Contact,
  NotificationLanguage,
} from '../../types';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { MyNotificationRules } from './notifications/MyNotificationRules';
import { NotificationChannelsPanel } from './notifications/NotificationChannelsPanel';
import { NotificationRulesPanel } from './notifications/NotificationRulesPanel';
import { useNotificationChannels } from './notifications/useNotificationChannels';

const SELECT_CLASS =
  'h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

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
  const notificationChannels = useNotificationChannels();

  return (
    <div className={className}>
      <MyNotificationRules />

      <NotificationChannelsPanel
        state={notificationChannels}
        appSettings={appSettings}
        onSaveAppSettings={onSaveAppSettings}
      />

      <Separator />

      <NotificationRulesPanel
        state={notificationChannels}
        contacts={contacts}
        chatChannels={channels}
      />

      <Separator />

      <div className="space-y-1">
        <Label htmlFor="notification-language">{t('settings.notifications.language.label')}</Label>
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
    </div>
  );
}
