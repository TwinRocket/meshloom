import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import type {
  FanoutDuplicate,
  NotificationChannel,
  NotificationEventInfo,
  NotificationRules,
  NotificationTypeInfo,
} from '../../../notifications/types';
import type { FieldErrors, JsonObject } from '../../forms/schemaFormModel';
import { Button } from '../../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../../ui/dialog';
import { toast } from '../../ui/sonner';
import { ChannelForm } from './ChannelForm';
import {
  createBody,
  fieldErrorsOf,
  iconFor,
  receivePatch,
  type ChannelDraft,
  type ReceivePreset,
} from './notificationsShared';

type Step = 'pick' | 'settings' | 'receive' | 'duplicates';

const PRESETS: ReceivePreset[] = ['like_push', 'alerts_only', 'everything'];

/**
 * Add a channel in three steps: pick a type or preset, fill its form (the **Test** runs
 * before anything is saved), then choose what the channel receives. The type forms are the
 * shared `SchemaForm` fed with the JSON Schemas of `GET /notifications/types`.
 *
 * When the new channel sends where an active fanout Apprise row already sends (the
 * response's `fanout_duplicates`, MSHL-134), a last step offers to convert those rows into
 * it, so each message is not notified twice.
 */
export function AddChannelDialog({
  types,
  channels,
  rules,
  events,
  onClose,
  onCreated,
}: {
  types: NotificationTypeInfo[];
  channels: NotificationChannel[];
  rules: NotificationRules | null;
  events: NotificationEventInfo[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>('pick');
  const [entry, setEntry] = useState<NotificationTypeInfo | null>(null);
  const [general, setGeneral] = useState<string | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  // What was typed at the last Test: held in memory (never in the DOM) so a failed test
  // can still be skipped and the channel saved with the same values.
  const [pending, setPending] = useState<{ draft: ChannelDraft; config: JsonObject } | null>(null);
  const [tested, setTested] = useState(false);
  const [receive, setReceive] = useState<ReceivePreset>('like_push');
  const [busy, setBusy] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [duplicates, setDuplicates] = useState<FanoutDuplicate[]>([]);

  // A singleton type (Web Push) already exists; it is not offered again.
  const choices = types.filter((e) => !e.singleton);
  // A 4.18.x destination (`legacy:<type>`) owns its type: the rules of a second channel of
  // that type would answer 409 `legacy_managed`, so it is edited from its own card instead.
  const legacyTaken = (e: NotificationTypeInfo) =>
    !e.preset && channels.some((c) => c.id === `legacy:${e.type}`);

  const runTest = async (draft: ChannelDraft, config: JsonObject) => {
    if (!entry) return;
    setGeneral(null);
    setErrors({});
    setPending({ draft, config });
    setTested(false);
    try {
      await api.testNotificationConfig(createBody(entry, draft, config));
    } catch (err) {
      setGeneral(formatApiError(err, t));
      setErrors(fieldErrorsOf(err, t('settings.notifications.channels.fieldInvalid')));
      throw err;
    }
    setTested(true);
    toast.success(t('settings.notifications.channels.testSent'));
    setStep('receive');
  };

  const create = async () => {
    if (!entry || !pending) return;
    setBusy(true);
    setGeneral(null);
    let id: string;
    let found: FanoutDuplicate[];
    try {
      const created = await api.createNotificationChannel(
        createBody(entry, pending.draft, pending.config)
      );
      id = created.id;
      found = created.fanout_duplicates ?? [];
    } catch (err) {
      setGeneral(formatApiError(err, t));
      setErrors(fieldErrorsOf(err, t('settings.notifications.channels.fieldInvalid')));
      setBusy(false);
      return;
    }
    try {
      if (rules) {
        const patch = receivePatch(receive, id, rules, events);
        if (Object.keys(patch.events ?? {}).length > 0) await api.patchNotificationRules(patch);
      }
      toast.success(t('settings.notifications.channels.created'));
    } catch (err) {
      // The channel exists; only its rows failed (`legacy_managed` included).
      toast.error(t('settings.notifications.channels.rulesFailed'), {
        description: formatApiError(err, t),
      });
    }
    setBusy(false);
    onCreated();
    if (found.length > 0) {
      setCreatedId(id);
      setDuplicates(found);
      setStep('duplicates');
      return;
    }
    onClose();
  };

  const convertDuplicates = async () => {
    if (!createdId) return;
    setBusy(true);
    try {
      for (const row of duplicates) {
        await api.convertFanoutToChannel(row.id, createdId);
      }
      toast.success(
        t('settings.notifications.channels.fanoutDuplicate.converted', {
          count: duplicates.length,
        })
      );
      onCreated();
      onClose();
    } catch (err) {
      setGeneral(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90dvh] w-[calc(100vw-1.5rem)] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('settings.notifications.channels.addTitle')}</DialogTitle>
          <DialogDescription>{t(`settings.notifications.channels.step.${step}`)}</DialogDescription>
        </DialogHeader>

        {step === 'pick' && (
          <ul className="grid gap-2" data-testid="channel-type-picker">
            {choices.map((choice) => {
              const Icon = iconFor(choice.type);
              return (
                <li key={`${choice.preset ? 'p' : 't'}-${choice.type}`}>
                  <button
                    type="button"
                    disabled={legacyTaken(choice)}
                    onClick={() => {
                      setEntry(choice);
                      setStep('settings');
                    }}
                    className="flex w-full items-start gap-3 rounded-md border border-border/60 p-3 text-left hover:bg-accent disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
                  >
                    <Icon
                      className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span className="min-w-0 space-y-0.5">
                      <span className="block text-sm font-medium">
                        {t(`settings.notifications.types.${choice.type}.name`, {
                          defaultValue: choice.type,
                        })}
                      </span>
                      <span className="block text-[0.8125rem] text-muted-foreground">
                        {t(`settings.notifications.types.${choice.type}.description`, {
                          defaultValue: '',
                        })}
                      </span>
                      {legacyTaken(choice) && (
                        <span className="block text-[0.8125rem] text-warning">
                          {t('settings.notifications.channels.legacyTaken')}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {step === 'settings' && entry && (
          <ChannelForm
            entry={entry}
            idPrefix="add-channel"
            submitLabel={t('settings.notifications.channels.testAndContinue')}
            errors={errors}
            general={general}
            onSubmit={runTest}
          >
            {general && pending && !tested && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  setGeneral(null);
                  setStep('receive');
                }}
              >
                {t('settings.notifications.channels.skipTest')}
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setGeneral(null);
                setErrors({});
                setPending(null);
                setStep('pick');
              }}
            >
              {t('settings.notifications.channels.back')}
            </Button>
          </ChannelForm>
        )}

        {step === 'receive' && entry && (
          <div className="space-y-4">
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">
                {t('settings.notifications.channels.receiveTitle')}
              </legend>
              {PRESETS.map((preset) => (
                <label
                  key={preset}
                  className="flex cursor-pointer items-start gap-3 rounded-md border border-border/60 p-3"
                >
                  <input
                    type="radio"
                    name="receive-preset"
                    value={preset}
                    checked={receive === preset}
                    onChange={() => setReceive(preset)}
                    className="mt-1"
                  />
                  <span className="min-w-0 space-y-0.5">
                    <span className="block text-sm font-medium">
                      {t(`settings.notifications.channels.receive.${preset}`)}
                    </span>
                    <span className="block text-[0.8125rem] text-muted-foreground">
                      {t(`settings.notifications.channels.receive.${preset}Help`)}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
            {!tested && (
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.notifications.channels.untested')}
              </p>
            )}
            {general && (
              <p role="alert" className="text-sm text-destructive" data-testid="channel-form-error">
                {general}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={() => void create()} disabled={busy}>
                {t('settings.notifications.channels.add')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setGeneral(null);
                  setStep('settings');
                }}
              >
                {t('settings.notifications.channels.back')}
              </Button>
            </div>
          </div>
        )}
        {step === 'duplicates' && (
          <div className="space-y-4" data-testid="fanout-duplicate-prompt">
            <p className="text-sm">
              {t('settings.notifications.channels.fanoutDuplicate.body', {
                count: duplicates.length,
              })}
            </p>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {duplicates.map((row) => (
                <li key={row.id}>{row.name}</li>
              ))}
            </ul>
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.notifications.channels.fanoutDuplicate.help')}
            </p>
            {general && (
              <p role="alert" className="text-sm text-destructive">
                {general}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={() => void convertDuplicates()} disabled={busy}>
                {t('settings.notifications.channels.fanoutDuplicate.convert', {
                  count: duplicates.length,
                })}
              </Button>
              <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>
                {t('settings.notifications.channels.fanoutDuplicate.keep')}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
