import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { formatApiError } from '../../../api';
import { usePush } from '../../../contexts/PushSubscriptionContext';
import type { AppSettings, AppSettingsUpdate } from '../../../types';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { Separator } from '../../ui/separator';
import { toast } from '../../ui/sonner';
import { SettingsGroupHeader } from '../settingsPrimitives';

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

/**
 * The browser side of `builtin:push`: this device's subscription, the other devices, the
 * VAPID subject and the private-endpoint switch. It lives inside the push channel card.
 */
export function PushDevicesBlock({
  appSettings,
  onSaveAppSettings,
}: {
  appSettings: AppSettings | null;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
}) {
  const { t } = useTranslation();
  const {
    isSupported,
    allSubscriptions,
    preferences,
    loading,
    subscribe,
    currentSubscriptionId,
    patchPreferences,
    deleteSubscription,
    testPush,
    refreshSubscriptions,
  } = usePush();
  const [vapidDraft, setVapidDraft] = useState('');
  const [vapidError, setVapidError] = useState<string | null>(null);

  useEffect(() => {
    refreshSubscriptions();
  }, [refreshSubscriptions]);

  // Re-sync the draft when the saved value changes (adjust state during render).
  const [prevPreferences, setPrevPreferences] = useState<typeof preferences>(null);
  if (preferences !== prevPreferences) {
    setPrevPreferences(preferences);
    if (preferences) {
      setVapidDraft(preferences.vapid_subject);
      setVapidError(null);
    }
  }

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

  return (
    <div className="space-y-4" data-testid="push-devices-block">
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

      <Separator />

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
        {appSettings?.push_private_endpoints_grandfathered === true && (
          <div
            className="flex flex-wrap items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-2 text-[0.8125rem]"
            role="status"
          >
            <p className="min-w-0 flex-1">{t('settings.notifications.pushGrandfathered')}</p>
            <Button
              variant="outline"
              size="sm"
              disabled={!onSaveAppSettings}
              onClick={() => {
                void onSaveAppSettings?.({ push_private_endpoints_grandfathered: false }).catch(
                  (err: unknown) =>
                    toast.error(t('settings.notifications.pushAllowPrivateSaveFailed'), {
                      description: formatApiError(err, t),
                    })
                );
              }}
            >
              {t('settings.notifications.webhookGrandfatheredDismiss')}
            </Button>
          </div>
        )}
        {appSettings?.push_private_endpoints_review === true && (
          <div
            className="flex flex-wrap items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-2 text-[0.8125rem]"
            role="status"
          >
            <p className="min-w-0 flex-1">{t('settings.notifications.pushReview')}</p>
            <Button
              variant="outline"
              size="sm"
              disabled={!onSaveAppSettings}
              onClick={() => {
                void onSaveAppSettings?.({ push_allow_private_endpoints: true }).catch(
                  (err: unknown) =>
                    toast.error(t('settings.notifications.pushAllowPrivateSaveFailed'), {
                      description: formatApiError(err, t),
                    })
                );
              }}
            >
              {t('settings.notifications.pushReviewEnable')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!onSaveAppSettings}
              onClick={() => {
                void onSaveAppSettings?.({ push_private_endpoints_review: false }).catch(
                  (err: unknown) =>
                    toast.error(t('settings.notifications.pushAllowPrivateSaveFailed'), {
                      description: formatApiError(err, t),
                    })
                );
              }}
            >
              {t('settings.notifications.pushReviewDismiss')}
            </Button>
          </div>
        )}
        <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
          <Checkbox
            id="push-allow-private-endpoints"
            checked={appSettings?.push_allow_private_endpoints === true}
            disabled={!onSaveAppSettings || !appSettings}
            onCheckedChange={(checked) => {
              void onSaveAppSettings?.({ push_allow_private_endpoints: checked === true }).catch(
                (err: unknown) =>
                  toast.error(t('settings.notifications.pushAllowPrivateSaveFailed'), {
                    description: formatApiError(err, t),
                  })
              );
            }}
            className="mt-0.5"
          />
          <div className="space-y-1">
            <Label htmlFor="push-allow-private-endpoints">
              {t('settings.notifications.pushAllowPrivate')}
            </Label>
            <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
              {t('settings.notifications.pushAllowPrivateHelp')}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
