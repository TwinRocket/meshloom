import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import {
  DEFAULT_NOTIFICATION_DESTINATIONS,
  SECRET_REDACTED,
  buildNotificationDestinationsPatch,
  isAppriseDestinationReady,
  isEmailDestinationReady,
  isWebhookDestinationReady,
  resolveNotificationDestinations,
  type AppSettings,
  type AppSettingsUpdate,
  type AppriseFormat,
  type AppriseMessageText,
  type NotificationDestinationChannel,
  type NotificationDestinations,
  type NotificationEmailMode,
} from '../../../types';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { toast } from '../../ui/sonner';

const APPRISE_URL_SYNTAX = 'https://appriseit.com/getting-started/universal-syntax/';
const APPRISE_URL_EXAMPLES = [
  'ntfys://ntfy.sh/my-meshloom-topic',
  'tgram://bot_token/chat_id',
  'discord://webhook_id/webhook_token',
  'hassios://homeassistant.local:8123/long_lived_token',
];
const SELECT_CLASS =
  'h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

const DEFAULT_BLANK = {
  email: { host: '', port: 587, mode: 'starttls', user: '', password: '', from: '', to: '' },
  webhook: {
    url: '',
    hmac_secret: '',
    allow_private_network: false,
    private_network_grandfathered: false,
  },
  apprise: {
    urls: '',
    allow_private_network: false,
    include_message_text: 'public_only',
    format: 'text',
  },
} as const;

export type LegacyMedium = NotificationDestinationChannel;

/**
 * The 4.18.x destination forms (`notification_destinations` in `PATCH /api/settings`), kept for
 * the `legacy:*` channels: the new channel routes answer 409 `legacy_managed` for their
 * configuration and deletion until the producers read the new rules. Fields save on blur.
 */
export function LegacyDestinationForm({
  medium,
  appSettings,
  onSaveAppSettings,
}: {
  medium: LegacyMedium;
  appSettings: AppSettings | null;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
}) {
  const { t } = useTranslation();
  const storedDest = resolveNotificationDestinations(appSettings?.notification_destinations);
  const [destDraft, setDestDraft] = useState<NotificationDestinations>(storedDest);
  // Last saved destinations: the ref serves handlers (read synchronously between
  // chained saves), the state mirror serves render (secret placeholders).
  const destRef = useRef(storedDest);
  const [savedDest, setSavedDest] = useState<NotificationDestinations>(storedDest);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const [testingChannel, setTestingChannel] = useState<NotificationDestinationChannel | null>(null);

  const notificationDestinations = appSettings?.notification_destinations;
  const [prevNotificationDestinations, setPrevNotificationDestinations] =
    useState(notificationDestinations);
  if (notificationDestinations !== prevNotificationDestinations) {
    setPrevNotificationDestinations(notificationDestinations);
    const next = resolveNotificationDestinations(notificationDestinations);
    setSavedDest(next);
    setDestDraft(next);
  }

  useEffect(() => {
    destRef.current = resolveNotificationDestinations(notificationDestinations);
  }, [notificationDestinations]);

  const persistDestinations = (next: NotificationDestinations, revert: () => void) => {
    if (!onSaveAppSettings) return;
    const previous = destRef.current;
    destRef.current = next;
    setSavedDest(next);
    const chained = saveChainRef.current.then(async () => {
      try {
        await onSaveAppSettings({
          notification_destinations: buildNotificationDestinationsPatch(next, previous),
        });
      } catch (err) {
        destRef.current = previous;
        setSavedDest(previous);
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

  // The stored URL is write-only (SECRET_REDACTED); the API adds its scheme and host.
  const webhookUrlKept = destDraft.webhook.url === SECRET_REDACTED;
  const webhookUrlRedacted = savedDest.webhook.url_redacted ?? '';

  /** Clears the URL and its HMAC secret at once: a kept secret never follows a new URL. */
  const clearWebhookUrl = () => {
    const previous = destDraft;
    const next = { ...destDraft, webhook: { ...destDraft.webhook, url: '', hmac_secret: '' } };
    setDestDraft(next);
    persistDestinations(next, () => setDestDraft(previous));
  };

  const commitAppriseField = <K extends keyof NotificationDestinations['apprise']>(
    field: K,
    value: NotificationDestinations['apprise'][K]
  ) => {
    if (
      destDraft.apprise[field] === destRef.current.apprise[field] &&
      destDraft.apprise[field] === value
    ) {
      return;
    }
    const previous = destDraft;
    const next = { ...destDraft, apprise: { ...destDraft.apprise, [field]: value } };
    setDestDraft(next);
    persistDestinations(next, () => setDestDraft(previous));
  };

  const testDestination = async (channel: NotificationDestinationChannel) => {
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

  const appriseReady = isAppriseDestinationReady(destDraft.apprise);

  /** Empties the destination (a kept secret never follows a new value, so they go together). */
  const removeDestination = () => {
    const previous = destDraft;
    const blank = DEFAULT_BLANK[medium];
    const next = { ...destDraft, [medium]: blank } as NotificationDestinations;
    setDestDraft(next);
    persistDestinations(next, () => setDestDraft(previous));
  };

  return (
    <div className="space-y-3" data-testid={`legacy-form-${medium}`}>
      {medium === 'email' && (
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
                className={SELECT_CLASS}
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
                  savedDest.email.password === SECRET_REDACTED
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
      )}
      {medium === 'webhook' && (
        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.notifications.webhookTitle')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.webhookNotFanout')}
          </p>
          {savedDest.webhook.private_network_grandfathered && (
            <div
              className="flex flex-wrap items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-2 text-[0.8125rem]"
              role="status"
            >
              <p className="min-w-0 flex-1">{t('settings.notifications.webhookGrandfathered')}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => commitWebhookField('private_network_grandfathered', false)}
              >
                {t('settings.notifications.webhookGrandfatheredDismiss')}
              </Button>
            </div>
          )}
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="space-y-1.5 lg:col-span-2">
              <Label htmlFor="notify-webhook-url">{t('settings.notifications.webhookUrl')}</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="notify-webhook-url"
                  type="url"
                  value={webhookUrlKept ? '' : destDraft.webhook.url}
                  autoComplete="off"
                  placeholder={
                    webhookUrlKept
                      ? t('settings.notifications.webhookUrlKept', { url: webhookUrlRedacted })
                      : undefined
                  }
                  onChange={(event) =>
                    setDestDraft((prev) => ({
                      ...prev,
                      webhook: {
                        ...prev.webhook,
                        // Emptied while a URL is stored: back to "unchanged".
                        url:
                          event.target.value === '' &&
                          destRef.current.webhook.url === SECRET_REDACTED
                            ? SECRET_REDACTED
                            : event.target.value,
                      },
                    }))
                  }
                  onBlur={() => commitWebhookField('url', destDraft.webhook.url.trim())}
                />
                {webhookUrlKept && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    title={t('settings.notifications.webhookUrlClearHelp')}
                    onClick={clearWebhookUrl}
                  >
                    {t('settings.notifications.webhookUrlClear')}
                  </Button>
                )}
              </div>
            </div>
            <div className="space-y-1.5 lg:col-span-2">
              <Label htmlFor="notify-webhook-hmac">{t('settings.notifications.webhookHmac')}</Label>
              <Input
                id="notify-webhook-hmac"
                type="password"
                autoComplete="new-password"
                value={destDraft.webhook.hmac_secret}
                placeholder={
                  savedDest.webhook.hmac_secret === SECRET_REDACTED
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
            <div className="flex items-start gap-3 rounded-md border border-border/60 p-3 lg:col-span-2">
              <Checkbox
                id="notify-webhook-allow-private"
                checked={destDraft.webhook.allow_private_network}
                onCheckedChange={(checked) =>
                  commitWebhookField('allow_private_network', checked === true)
                }
                className="mt-0.5"
              />
              <div className="space-y-1">
                <Label htmlFor="notify-webhook-allow-private">
                  {t('settings.notifications.webhookAllowPrivate')}
                </Label>
                <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                  {t('settings.notifications.webhookAllowPrivateHelp')}
                </p>
              </div>
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
      )}
      {medium === 'apprise' && (
        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.notifications.appriseTitle')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.appriseHelp')}{' '}
            <a
              href={APPRISE_URL_SYNTAX}
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-foreground"
            >
              {t('settings.notifications.appriseWiki')}
            </a>
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="notify-apprise-urls">{t('settings.notifications.appriseUrls')}</Label>
            <textarea
              id="notify-apprise-urls"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[80px]"
              autoComplete="off"
              spellCheck={false}
              rows={3}
              value={destDraft.apprise.urls}
              placeholder={APPRISE_URL_EXAMPLES.join('\n')}
              onChange={(event) =>
                setDestDraft((prev) => ({
                  ...prev,
                  apprise: { ...prev.apprise, urls: event.target.value },
                }))
              }
              onBlur={() => commitAppriseField('urls', destDraft.apprise.urls.trim())}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              {savedDest.apprise.urls === SECRET_REDACTED
                ? t('settings.notifications.appriseUrlsKept')
                : t('settings.notifications.appriseUrlsHelp')}
            </p>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="notify-apprise-text">{t('settings.notifications.appriseText')}</Label>
              <select
                id="notify-apprise-text"
                value={destDraft.apprise.include_message_text}
                onChange={(event) =>
                  commitAppriseField(
                    'include_message_text',
                    event.target.value as AppriseMessageText
                  )
                }
                className={SELECT_CLASS}
              >
                <option value="public_only">
                  {t('settings.notifications.appriseTextPublicOnly')}
                </option>
                <option value="always">{t('settings.notifications.appriseTextAlways')}</option>
                <option value="never">{t('settings.notifications.appriseTextNever')}</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-apprise-format">
                {t('settings.notifications.appriseFormat')}
              </Label>
              <select
                id="notify-apprise-format"
                value={destDraft.apprise.format}
                onChange={(event) =>
                  commitAppriseField('format', event.target.value as AppriseFormat)
                }
                className={SELECT_CLASS}
              >
                <option value="text">{t('settings.notifications.appriseFormatText')}</option>
                <option value="markdown">
                  {t('settings.notifications.appriseFormatMarkdown')}
                </option>
              </select>
            </div>
            <p className="max-w-prose text-[0.8125rem] text-muted-foreground lg:col-span-2">
              {t('settings.notifications.apprisePrivacy')}
            </p>
            <div className="flex items-start gap-3 rounded-md border border-border/60 p-3 lg:col-span-2">
              <Checkbox
                id="notify-apprise-allow-private"
                checked={destDraft.apprise.allow_private_network}
                onCheckedChange={(checked) =>
                  commitAppriseField('allow_private_network', checked === true)
                }
                className="mt-0.5"
              />
              <div className="space-y-1">
                <Label htmlFor="notify-apprise-allow-private">
                  {t('settings.notifications.appriseAllowPrivate')}
                </Label>
                <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                  {t('settings.notifications.appriseAllowPrivateHelp')}
                </p>
              </div>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={!appriseReady || testingChannel !== null}
            onClick={() => void testDestination('apprise')}
          >
            {testingChannel === 'apprise'
              ? t('settings.notifications.destTesting')
              : t('settings.notifications.testApprise')}
          </Button>
        </div>
      )}
      <Button
        variant="outline"
        size="sm"
        className="text-destructive hover:text-destructive"
        disabled={!onSaveAppSettings}
        onClick={removeDestination}
      >
        {t('settings.notifications.channels.legacyRemove')}
      </Button>
    </div>
  );
}
