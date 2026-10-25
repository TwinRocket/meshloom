import { useId, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  NotificationChannel,
  NotificationIncludeText,
  NotificationTypeInfo,
} from '../../../notifications/types';
import { SchemaForm } from '../../forms/SchemaForm';
import { i18nKeyResolver, type FieldErrors, type JsonObject } from '../../forms/schemaFormModel';
import { Checkbox } from '../../ui/checkbox';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import {
  fieldTextKeys,
  secretNames,
  typePrefix,
  visibleSchema,
  type ChannelDraft,
} from './notificationsShared';

const SELECT_CLASS =
  'h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

/** The channel-level settings the JSON Schema does not carry, from the stored channel or the type defaults. */
export function initialDraft(
  entry: NotificationTypeInfo,
  channel?: NotificationChannel | null
): ChannelDraft {
  return {
    name: channel?.name ?? '',
    language: channel?.language ?? 'auto',
    include_message_text:
      channel?.include_message_text ?? entry.include_message_text_default ?? 'always',
    allow_private_network: channel?.allow_private_network ?? false,
    verify_tls: channel?.verify_tls ?? true,
  };
}

/**
 * The settings of one channel: the common fields (name, language, privacy, LAN, TLS) and the
 * type's own fields, which the shared `SchemaForm` renders from the JSON Schema of `/types`.
 * Validation is the server's: its coded answer comes back through `errors` and `general`.
 */
export function ChannelForm({
  entry,
  channel,
  onSubmit,
  submitLabel,
  errors,
  general,
  disabled,
  idPrefix,
  children,
}: {
  entry: NotificationTypeInfo;
  channel?: NotificationChannel | null;
  onSubmit: (draft: ChannelDraft, config: JsonObject) => void | Promise<void>;
  submitLabel: string;
  errors?: FieldErrors;
  general?: string | null;
  disabled?: boolean;
  idPrefix?: string;
  children?: ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const autoId = useId();
  const prefix = idPrefix ?? `channel-form-${autoId}`;
  const [draft, setDraft] = useState<ChannelDraft>(() => initialDraft(entry, channel));
  const schema = useMemo(() => visibleSchema(entry.schema), [entry.schema]);
  const textKeys = useMemo(() => fieldTextKeys(entry.schema), [entry.schema]);
  const keyPrefix = typePrefix(entry.type);
  const resolver = useMemo(() => {
    const lookup = (key: unknown) => {
      if (typeof key !== 'string') return undefined;
      const text = t(key, { defaultValue: '' });
      return text && text !== key ? text : undefined;
    };
    const base = i18nKeyResolver(t, keyPrefix, i18n.language);
    return {
      ...base,
      // The server names its labels and help by key (`label_key`, `help_key`).
      label: (name: string, s: JsonObject) => lookup(textKeys[name]?.label) ?? base.label(name, s),
      description: (name: string, s: JsonObject) =>
        lookup(textKeys[name]?.help) ?? base.description?.(name, s),
    };
  }, [t, keyPrefix, textKeys, i18n.language]);
  const secretsSet = useMemo(
    () =>
      channel ? secretNames(entry.schema).filter((n) => channel.secret_fields.includes(n)) : [],
    [channel, entry.schema]
  );
  const patch = (next: Partial<ChannelDraft>) => setDraft((prev) => ({ ...prev, ...next }));
  const showTls = !entry.preset && entry.type !== 'web_push' && entry.type !== 'apprise';

  return (
    <div className="space-y-4" data-testid="channel-form">
      <div className="space-y-1.5">
        <Label htmlFor={`${prefix}-name`}>{t('settings.notifications.channels.name')}</Label>
        <Input
          id={`${prefix}-name`}
          value={draft.name}
          maxLength={80}
          autoComplete="off"
          disabled={disabled}
          onChange={(event) => patch({ name: event.target.value })}
        />
      </div>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-language`}>
            {t('settings.notifications.channels.language')}
          </Label>
          <select
            id={`${prefix}-language`}
            value={draft.language}
            disabled={disabled}
            onChange={(event) =>
              patch({ language: event.target.value as ChannelDraft['language'] })
            }
            className={SELECT_CLASS}
          >
            <option value="auto">{t('settings.notifications.language.auto')}</option>
            <option value="fr">{t('settings.notifications.language.fr')}</option>
            <option value="en">{t('settings.notifications.language.en')}</option>
          </select>
        </div>

        {entry.cloud && (
          <div className="space-y-1.5">
            <p
              className="rounded-md border border-warning/40 bg-warning/10 p-2 text-[0.8125rem]"
              data-testid="cloud-privacy-note"
            >
              {t('settings.notifications.channels.cloudNote')}
            </p>
            <Label htmlFor={`${prefix}-text`}>
              {t('settings.notifications.channels.includeText')}
            </Label>
            <select
              id={`${prefix}-text`}
              value={draft.include_message_text}
              disabled={disabled || entry.include_message_text_locked}
              onChange={(event) =>
                patch({ include_message_text: event.target.value as NotificationIncludeText })
              }
              className={SELECT_CLASS}
            >
              <option value="public_only">
                {t('settings.notifications.channels.includeTextPublicOnly')}
              </option>
              <option value="always">
                {t('settings.notifications.channels.includeTextAlways')}
              </option>
              <option value="never">{t('settings.notifications.channels.includeTextNever')}</option>
            </select>
            {entry.include_message_text_locked && (
              <p
                className="text-[0.8125rem] text-muted-foreground"
                data-testid="text-policy-locked"
              >
                {t('settings.notifications.channels.includeTextLocked')}
              </p>
            )}
          </div>
        )}

        {entry.type !== 'web_push' && (
          <div className="flex items-start gap-3">
            <Checkbox
              id={`${prefix}-lan`}
              checked={draft.allow_private_network}
              disabled={disabled}
              onCheckedChange={(checked) => patch({ allow_private_network: checked === true })}
              className="mt-0.5"
            />
            <div className="min-w-0 space-y-1">
              <Label htmlFor={`${prefix}-lan`}>{t('settings.notifications.channels.lan')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.notifications.channels.lanWarning')}
              </p>
            </div>
          </div>
        )}

        {showTls && (
          <div className="flex items-start gap-3">
            <Checkbox
              id={`${prefix}-tls`}
              checked={draft.verify_tls}
              disabled={disabled}
              onCheckedChange={(checked) => patch({ verify_tls: checked === true })}
              className="mt-0.5"
            />
            <div className="min-w-0 space-y-1">
              <Label htmlFor={`${prefix}-tls`}>
                {t('settings.notifications.channels.verifyTls')}
              </Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.notifications.channels.verifyTlsHelp')}
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-border/60 pt-3">
        {entry.help_key && (
          <p className="mb-3 text-[0.8125rem] text-muted-foreground" data-testid="preset-about">
            {t(entry.help_key, { defaultValue: '' })}
            {entry.wiki_url && (
              <>
                {' '}
                <a
                  href={entry.wiki_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline underline-offset-2"
                >
                  {t('settings.notifications.channels.presetDocs')}
                </a>
              </>
            )}
          </p>
        )}
        <SchemaForm
          key={
            channel
              ? `${channel.id}-${channel.created_at}-${channel.secret_fields.join(',')}`
              : 'new'
          }
          idPrefix={`${prefix}-f`}
          schema={schema}
          values={(channel?.config ?? {}) as JsonObject}
          secretsSet={secretsSet}
          resolver={resolver}
          onSubmit={(config) => onSubmit({ ...draft, name: draft.name.trim() }, config)}
          submitLabel={submitLabel}
          errors={errors}
          disabled={disabled}
        />
      </div>

      {general && (
        <p role="alert" className="text-sm text-destructive" data-testid="channel-form-error">
          {general}
        </p>
      )}
      {children}
    </div>
  );
}
