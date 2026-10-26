import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { SchemaForm } from '../../forms/SchemaForm';
import { languageMapResolver, type FieldErrors } from '../../forms/schemaFormModel';
import { toast } from '../../ui/sonner';
import { configIssueErrors } from './configErrors';
import { attentionFields } from './pluginsShared';
import type { PluginInstance } from './pluginsTypes';

/** The mask the API returns for a secret that holds a value. */
const SECRET_MASK = '********';

/**
 * The saved config of an instance as a `SchemaForm` fed with its `config.schema.json`.
 * Secrets are write-only: never prefilled, kept when left alone (the API keeps a secret
 * that is omitted), replaced or cleared on request.
 */
export function PluginConfigForm({
  instance,
  locked,
  onChanged,
}: {
  instance: PluginInstance;
  locked: boolean;
  onChanged: (instance: PluginInstance) => void;
}) {
  const { t, i18n } = useTranslation();
  const [errors, setErrors] = useState<FieldErrors>({});
  const [general, setGeneral] = useState<string | null>(null);
  const properties = instance.config_schema.properties;
  const hasFields =
    typeof properties === 'object' && properties !== null && Object.keys(properties).length > 0;
  const resolver = useMemo(() => languageMapResolver(i18n.language), [i18n.language]);
  const secretsSet = useMemo(
    () => instance.secret_fields.filter((name) => instance.config[name] === SECRET_MASK),
    [instance.secret_fields, instance.config]
  );

  if (!hasFields) {
    return <p className="text-sm text-muted-foreground">{t('settings.extensions.config.none')}</p>;
  }

  const save = async (values: Record<string, unknown>) => {
    setErrors({});
    setGeneral(null);
    try {
      onChanged(await api.patchPlugin(instance.instance_id, { config: values }));
      toast.success(t('settings.extensions.config.saved'));
    } catch (err) {
      const fieldErrors = configIssueErrors(err, t);
      if (fieldErrors) setErrors(fieldErrors);
      else setGeneral(formatApiError(err, t));
      throw err;
    }
  };

  return (
    <div className="space-y-2" id={`plugin-config-${instance.instance_id}`}>
      {general && (
        <p role="alert" className="text-sm text-destructive">
          {general}
        </p>
      )}
      <SchemaForm
        // Remount after a save: the stored values (and no typed secret) start over.
        key={`${instance.instance_id}-${instance.updated_at}`}
        idPrefix={`plugin-${instance.instance_id}`}
        schema={instance.config_schema}
        values={instance.config}
        secretsSet={secretsSet}
        resolver={resolver}
        onSubmit={save}
        submitLabel={t('settings.extensions.config.save')}
        errors={errors}
        disabled={locked}
        flagged={attentionFields(instance)}
      />
    </div>
  );
}

/** Scrolls to the config form and focuses its first flagged (or first) field. */
export function focusConfigForm(instanceId: string): void {
  const root = document.getElementById(`plugin-config-${instanceId}`);
  if (!root) return;
  root.scrollIntoView?.({ block: 'center' });
  const target =
    root.querySelector<HTMLElement>('[data-flagged] input, [data-flagged] select') ??
    root.querySelector<HTMLElement>('input, select, textarea');
  target?.focus();
}
