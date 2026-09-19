import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { toast } from '../ui/sonner';
import { api, formatApiError } from '../../api';
import { usePush } from '../../contexts/PushSubscriptionContext';
import {
  DEFAULT_NOTIFICATION_DESTINATIONS,
  SECRET_REDACTED,
  buildNotificationDestinationsPatch,
  isEmailDestinationReady,
  isWebhookDestinationReady,
  resolveNotificationDestinations,
  type AppSettings,
  type AppSettingsUpdate,
  type Channel,
  type Contact,
  type NotificationDestinations,
  type NotificationEmailMode,
  type PushDefaults,
} from '../../types';
import { getContactDisplayName } from '../../utils/pubkey';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { SettingsGroupHeader } from './settingsPrimitives';

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
];

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

function normalizeVapidSubject(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return '';
  if (trimmed.toLowerCase().startsWith('mailto:')) return trimmed;
  if (trimmed.toLowerCase().startsWith('https:')) {
    try {
      const url = new URL(trimmed);
      if (url.protocol !== 'https:' || !url.hostname) return trimmed;
      return `https://${url.host}`;
    } catch {
      return trimmed;
    }
  }
  return trimmed;
}

function isValidVapidSubject(value: string): boolean {
  const normalized = normalizeVapidSubject(value);
  if (!normalized) return true;
  if (normalized.toLowerCase().startsWith('mailto:')) {
    const rest = normalized.slice('mailto:'.length);
    const at = rest.indexOf('@');
    return at > 0 && at < rest.length - 1;
  }
  if (normalized.toLowerCase().startsWith('https://')) {
    try {
      const url = new URL(normalized);
      return url.protocol === 'https:' && Boolean(url.hostname);
    } catch {
      return false;
    }
  }
  return false;
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
  const {
    isSupported,
    allSubscriptions,
    preferences,
    overrideEntries,
    loading,
    subscribe,
    currentSubscriptionId,
    setConversationOverride,
    patchPreferences,
    deleteSubscription,
    testPush,
    refreshSubscriptions,
  } = usePush();

  const [vapidDraft, setVapidDraft] = useState('');
  const [vapidError, setVapidError] = useState<string | null>(null);
  const storedDest = resolveNotificationDestinations(appSettings?.notification_destinations);
  const [destDraft, setDestDraft] = useState<NotificationDestinations>(storedDest);
  const destRef = useRef(storedDest);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const [testingChannel, setTestingChannel] = useState<'email' | 'webhook' | null>(null);

  useEffect(() => {
    refreshSubscriptions();
  }, [refreshSubscriptions]);

  useEffect(() => {
    if (preferences) {
      setVapidDraft(preferences.vapid_subject);
      setVapidError(null);
    }
  }, [preferences]);

  useEffect(() => {
    const next = resolveNotificationDestinations(appSettings?.notification_destinations);
    destRef.current = next;
    setDestDraft(next);
  }, [appSettings?.notification_destinations]);

  const commitVapidSubject = async () => {
    if (!isValidVapidSubject(vapidDraft)) {
      setVapidError(t('settings.notifications.vapidSubjectInvalid'));
      return false;
    }
    const next = normalizeVapidSubject(vapidDraft);
    setVapidError(null);
    if (next !== vapidDraft) setVapidDraft(next);
    if (next === (preferences?.vapid_subject ?? '')) return true;
    await patchPreferences({ vapid_subject: next });
    return true;
  };

  const persistDestinations = (next: NotificationDestinations, revert: () => void) => {
    if (!onSaveAppSettings) return;
    const previous = destRef.current;
    destRef.current = next;
    const chained = saveChainRef.current.then(async () => {
      try {
        await onSaveAppSettings({
          notification_destinations: buildNotificationDestinationsPatch(next, previous),
        });
      } catch (err) {
        destRef.current = previous;
        revert();
        toast.error(t('settings.notifications.destSaveFailed'), {
          description: formatApiError(err, t) || t('settings.notifications.unknownError'),
        });
      }
    });
    saveChainRef.current = chained;
  };

  const commitDestField = <K extends keyof NotificationDestinations['email']>(
    field: K,
    value: NotificationDestinations['email'][K]
  ) => {
    if (
      destDraft.email[field] === destRef.current.email[field] &&
      destDraft.email[field] === value
    ) {
      return;
    }
    const previous = destDraft;
    const next = { ...destDraft, email: { ...destDraft.email, [field]: value } };
    setDestDraft(next);
    persistDestinations(next, () => setDestDraft(previous));
  };

  const commitWebhookField = <K extends keyof NotificationDestinations['webhook']>(
    field: K,
    value: NotificationDestinations['webhook'][K]
  ) => {
    if (
      destDraft.webhook[field] === destRef.current.webhook[field] &&
      destDraft.webhook[field] === value
    ) {
      return;
    }
    const previous = destDraft;
    const next = { ...destDraft, webhook: { ...destDraft.webhook, [field]: value } };
    setDestDraft(next);
    persistDestinations(next, () => setDestDraft(previous));
  };

  const testDestination = async (channel: 'email' | 'webhook') => {
    setTestingChannel(channel);
    try {
      await api.testNotificationDestination(channel);
      toast.success(t('settings.notifications.destTestSent', { channel }));
    } catch (err) {
      toast.error(t('settings.notifications.destTestFailed', { channel }), {
        description: formatApiError(err, t),
      });
    } finally {
      setTestingChannel(null);
    }
  };

  return (
    <div className={className}>
      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.notifications.thisDevice')}
          storedOn="server"
          instant
        />
        {!isSupported ? (
          <p className="text-[0.8125rem] text-muted-foreground">
            {window.isSecureContext
              ? t('settings.notifications.unsupported')
              : t('settings.notifications.needsHttps')}
          </p>
        ) : (
          <>
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.notifications.thisDeviceHelp')}
            </p>
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.notifications.thisDeviceGlobalHelp')}
            </p>
            {!currentSubscriptionId && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void subscribe()}
                disabled={loading}
              >
                {loading
                  ? t('settings.notifications.subscribing')
                  : t('settings.notifications.subscribe')}
              </Button>
            )}
            {allSubscriptions.length > 0 && (
              <div className="space-y-2">
                <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                  {t('settings.notifications.devices')}
                </span>
                <div className="mt-2 space-y-2">
                  {allSubscriptions.map((sub) => (
                    <div
                      key={sub.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 overflow-hidden">
                          <span className="truncate text-sm font-medium">
                            {sub.label || t('settings.notifications.unknownDevice')}
                          </span>
                          {sub.id === currentSubscriptionId && (
                            <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium text-primary">
                              {t('settings.notifications.currentDevice')}
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {sub.last_success_at
                            ? t('settings.notifications.lastPush', {
                                date: new Date(sub.last_success_at * 1000).toLocaleDateString(),
                              })
                            : t('settings.notifications.neverPushed')}
                          {sub.failure_count > 0 &&
                            t('settings.notifications.failures', { count: sub.failure_count })}
                        </span>
                      </div>
                      <div className="flex gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 text-sm"
                          onClick={() => {
                            void (async () => {
                              const saved = await commitVapidSubject();
                              if (saved) await testPush(sub.id);
                            })();
                          }}
                        >
                          {t('settings.notifications.test')}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 text-sm text-destructive hover:text-destructive"
                          onClick={() => {
                            void deleteSubscription(sub.id).then(() =>
                              toast.success(t('settings.notifications.deviceRemoved'))
                            );
                          }}
                        >
                          {t('settings.notifications.unsubscribeDevice')}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.notifications.destinations')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.destinationsHelp')}
        </p>

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.notifications.emailTitle')}</h4>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="notify-email-host">{t('settings.notifications.emailHost')}</Label>
              <Input
                id="notify-email-host"
                value={destDraft.email.host}
                autoComplete="off"
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    email: { ...prev.email, host: event.target.value },
                  }))
                }
                onBlur={() => commitDestField('host', destDraft.email.host.trim())}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-email-port">{t('settings.notifications.emailPort')}</Label>
              <Input
                id="notify-email-port"
                type="number"
                inputMode="numeric"
                value={destDraft.email.port}
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    email: { ...prev.email, port: Number(event.target.value) || 0 },
                  }))
                }
                onBlur={() => {
                  const port = Number.isFinite(destDraft.email.port)
                    ? destDraft.email.port
                    : DEFAULT_NOTIFICATION_DESTINATIONS.email.port;
                  commitDestField('port', port);
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-email-mode">{t('settings.notifications.emailMode')}</Label>
              <select
                id="notify-email-mode"
                value={destDraft.email.mode}
                onChange={(event) => {
                  const mode = event.target.value as NotificationEmailMode;
                  commitDestField('mode', mode);
                }}
                className="h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
              >
                <option value="none">{t('settings.notifications.emailModeNone')}</option>
                <option value="starttls">{t('settings.notifications.emailModeStarttls')}</option>
                <option value="ssl">{t('settings.notifications.emailModeSsl')}</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-email-user">{t('settings.notifications.emailUser')}</Label>
              <Input
                id="notify-email-user"
                value={destDraft.email.user}
                autoComplete="off"
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    email: { ...prev.email, user: event.target.value },
                  }))
                }
                onBlur={() => commitDestField('user', destDraft.email.user)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-email-password">
                {t('settings.notifications.emailPassword')}
              </Label>
              <Input
                id="notify-email-password"
                type="password"
                autoComplete="new-password"
                value={destDraft.email.password}
                placeholder={
                  destRef.current.email.password === SECRET_REDACTED
                    ? t('settings.notifications.secretKept')
                    : undefined
                }
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    email: { ...prev.email, password: event.target.value },
                  }))
                }
                onBlur={() => commitDestField('password', destDraft.email.password)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-email-from">{t('settings.notifications.emailFrom')}</Label>
              <Input
                id="notify-email-from"
                value={destDraft.email.from}
                autoComplete="off"
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    email: { ...prev.email, from: event.target.value },
                  }))
                }
                onBlur={() => commitDestField('from', destDraft.email.from.trim())}
              />
            </div>
            <div className="space-y-1.5 lg:col-span-2">
              <Label htmlFor="notify-email-to">{t('settings.notifications.emailTo')}</Label>
              <Input
                id="notify-email-to"
                value={destDraft.email.to}
                autoComplete="off"
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    email: { ...prev.email, to: event.target.value },
                  }))
                }
                onBlur={() => commitDestField('to', destDraft.email.to.trim())}
              />
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={!isEmailDestinationReady(destDraft.email) || testingChannel !== null}
            onClick={() => void testDestination('email')}
          >
            {testingChannel === 'email'
              ? t('settings.notifications.destTesting')
              : t('settings.notifications.testEmail')}
          </Button>
        </div>

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.notifications.webhookTitle')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.webhookNotFanout')}
          </p>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="space-y-1.5 lg:col-span-2">
              <Label htmlFor="notify-webhook-url">{t('settings.notifications.webhookUrl')}</Label>
              <Input
                id="notify-webhook-url"
                value={destDraft.webhook.url}
                autoComplete="off"
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    webhook: { ...prev.webhook, url: event.target.value },
                  }))
                }
                onBlur={() => commitWebhookField('url', destDraft.webhook.url.trim())}
              />
            </div>
            <div className="space-y-1.5 lg:col-span-2">
              <Label htmlFor="notify-webhook-hmac">{t('settings.notifications.webhookHmac')}</Label>
              <Input
                id="notify-webhook-hmac"
                type="password"
                autoComplete="new-password"
                value={destDraft.webhook.hmac_secret}
                placeholder={
                  destRef.current.webhook.hmac_secret === SECRET_REDACTED
                    ? t('settings.notifications.secretKept')
                    : undefined
                }
                onChange={(event) =>
                  setDestDraft((prev) => ({
                    ...prev,
                    webhook: { ...prev.webhook, hmac_secret: event.target.value },
                  }))
                }
                onBlur={() => commitWebhookField('hmac_secret', destDraft.webhook.hmac_secret)}
              />
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={!isWebhookDestinationReady(destDraft.webhook) || testingChannel !== null}
            onClick={() => void testDestination('webhook')}
          >
            {testingChannel === 'webhook'
              ? t('settings.notifications.destTesting')
              : t('settings.notifications.testWebhook')}
          </Button>
        </div>
      </div>

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
        {DEFAULT_KEYS.map(({ key, label, help }) => (
          <div key={key} className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id={`push-default-${key}`}
              checked={preferences?.defaults[key] === true}
              disabled={!preferences}
              onCheckedChange={(checked) => {
                void patchPreferences({ defaults: { [key]: checked === true } });
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor={`push-default-${key}`}>{t(label)}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">{t(help)}</p>
            </div>
          </div>
        ))}
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
              const forcedOn = preferences?.overrides[key] === true;
              return (
                <span
                  key={key}
                  className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-sm"
                >
                  <span>{name}</span>
                  <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                    {forcedOn
                      ? t('settings.notifications.overrideOn')
                      : t('settings.notifications.overrideOff')}
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

      <Separator />

      <div className="space-y-2">
        <SettingsGroupHeader
          title={t('settings.notifications.vapidSubject')}
          storedOn="server"
          instant
        />
        <Label htmlFor="vapid-subject">{t('settings.notifications.vapidSubjectLabel')}</Label>
        <Input
          id="vapid-subject"
          type="text"
          autoComplete="off"
          placeholder={t('settings.notifications.vapidSubjectPlaceholder')}
          value={vapidDraft}
          disabled={!preferences}
          onChange={(event) => {
            setVapidDraft(event.target.value);
            if (vapidError) setVapidError(null);
          }}
          onBlur={() => void commitVapidSubject()}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              void commitVapidSubject();
            }
          }}
        />
        {vapidError ? (
          <p className="text-xs text-destructive">{vapidError}</p>
        ) : (
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.vapidSubjectHelp')}
          </p>
        )}
      </div>
    </div>
  );
}
