import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import type {
  NotificationIncludeText,
  NotificationTemplatePreview,
} from '../../../notifications/types';
import { Label } from '../../ui/label';
import type { ChannelDraft } from './notificationsShared';

/** The variables of a template (`app/notifications/templates.py`, `TEMPLATE_VARIABLES`). */
export const TEMPLATE_VARIABLES = [
  'type',
  'text',
  'sender_name',
  'sender_key',
  'channel_name',
  'conversation_key',
  'hops',
  'hops_backticked',
  'hop_count',
  'rssi',
  'snr',
  'title',
] as const;

/** Mirrors `MAX_TEMPLATE_LENGTH` of the server, which owns the validation. */
export const MAX_TEMPLATE_LENGTH = 500;

const PREVIEW_DELAY_MS = 400;

const TEXTAREA_CLASS =
  'w-full min-h-16 px-3 py-2 rounded-md border border-input bg-background font-mono text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

/**
 * The message templates of a channel: one for direct messages, one for channel messages,
 * the variable list and a live preview that the server renders with the real renderer
 * (text policy and Markdown escaping included). Empty keeps the default body.
 */
export function TemplateFields({
  idPrefix,
  draft,
  markdown,
  includeText,
  language,
  disabled,
  onChange,
}: {
  idPrefix: string;
  draft: Pick<ChannelDraft, 'template_dm' | 'template_channel'>;
  markdown: boolean;
  includeText: NotificationIncludeText;
  language: ChannelDraft['language'];
  disabled?: boolean;
  onChange: (next: Partial<Pick<ChannelDraft, 'template_dm' | 'template_channel'>>) => void;
}) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState<NotificationTemplatePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .previewNotificationTemplate({
          template_dm: draft.template_dm,
          template_channel: draft.template_channel,
          markdown,
          include_message_text: includeText,
          language,
        })
        .then((result) => {
          if (cancelled) return;
          setPreview(result);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setPreview(null);
          setError(formatApiError(err, t));
        });
    }, PREVIEW_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [draft.template_dm, draft.template_channel, markdown, includeText, language, t]);

  const fields = [
    {
      key: 'template_dm',
      id: `${idPrefix}-template-dm`,
      label: t('settings.notifications.templates.dm'),
      sample: preview?.dm,
    },
    {
      key: 'template_channel',
      id: `${idPrefix}-template-channel`,
      label: t('settings.notifications.templates.channel'),
      sample: preview?.channel,
    },
  ] as const;

  return (
    <details
      className="rounded-md border border-border/60 p-3"
      data-testid="channel-templates"
      open={draft.template_dm !== '' || draft.template_channel !== ''}
    >
      <summary className="cursor-pointer text-sm font-medium">
        {t('settings.notifications.templates.title')}
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.templates.help')}
        </p>
        <p className="text-[0.8125rem] text-muted-foreground" data-testid="template-variables">
          {t('settings.notifications.templates.variables')}{' '}
          {TEMPLATE_VARIABLES.map((name) => (
            <code key={name} className="mr-1 rounded bg-muted px-1 py-0.5 text-xs">
              {`{${name}}`}
            </code>
          ))}
        </p>
        {fields.map((field) => (
          <div key={field.key} className="space-y-1.5">
            <Label htmlFor={field.id}>{field.label}</Label>
            <textarea
              id={field.id}
              value={draft[field.key]}
              maxLength={MAX_TEMPLATE_LENGTH}
              rows={2}
              spellCheck={false}
              disabled={disabled}
              placeholder={t('settings.notifications.templates.placeholder')}
              onChange={(event) => onChange({ [field.key]: event.target.value })}
              className={TEXTAREA_CLASS}
            />
            <div className="rounded-md border border-border bg-muted/30 p-2">
              <span className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                {t('settings.notifications.templates.preview')}
              </span>
              <p
                className="break-all text-xs"
                data-testid={`${field.key.replace('_', '-')}-preview`}
              >
                {field.sample ?? '…'}
              </p>
            </div>
          </div>
        ))}
        {error && (
          <p role="alert" className="text-sm text-destructive" data-testid="template-error">
            {error}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {markdown
            ? t('settings.notifications.templates.markdownNote')
            : t('settings.notifications.templates.plainNote')}
        </p>
      </div>
    </details>
  );
}
