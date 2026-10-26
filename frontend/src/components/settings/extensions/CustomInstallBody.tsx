import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { SchemaForm } from '../../forms/SchemaForm';
import { languageMapResolver, type FieldErrors } from '../../forms/schemaFormModel';
import { Button } from '../../ui/button';
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../ui/dialog';
import { toast } from '../../ui/sonner';
import { configIssueErrors, schemaHasFields } from './configErrors';
import { ConsentWarnings, PermissionList } from './PermissionList';
import { localizedText } from './pluginsShared';
import type { PluginInstance, StoreRepository } from './pluginsTypes';

/**
 * The consent screen of a plugin from a custom repository, then (when the release has a
 * config schema) the settings it asks before the first start. The grant sent is exactly
 * the permissions listed on the screen (the release's declared ones): the API refuses a
 * narrower one, and nothing beyond them is ever sent.
 */
export function CustomInstallBody({
  repo,
  onClose,
  onInstalled,
}: {
  repo: StoreRepository;
  onClose: () => void;
  onInstalled: (instance: PluginInstance) => void;
}) {
  const { t, i18n } = useTranslation();
  const [accepted, setAccepted] = useState(false);
  const [step, setStep] = useState<'consent' | 'config'>('consent');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const resolver = useMemo(() => languageMapResolver(i18n.language), [i18n.language]);
  const entry = repo.entry;
  if (!entry) return null;
  const name = localizedText(entry.name, i18n.language) || entry.plugin_id;
  const hasForm = schemaHasFields(entry.config_schema);

  const install = async (config?: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      const instance = await api.installPlugin({
        kind: 'custom',
        plugin_id: entry.plugin_id,
        repository: repo.repository,
        version: entry.version,
        granted_permissions: entry.permissions,
        ...(config ? { config } : {}),
      });
      toast.success(t('settings.extensions.add.installed', { name: instance.label }));
      onInstalled(instance);
    } catch (err) {
      const issues = configIssueErrors(err, t);
      if (issues) setFieldErrors(issues);
      else setError(formatApiError(err, t));
      throw err;
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t('settings.extensions.add.consentTitle', { name })}</DialogTitle>
        <DialogDescription>
          {step === 'consent'
            ? t('settings.extensions.add.consentIntro')
            : t('pluginStore.install.configIntro')}
        </DialogDescription>
      </DialogHeader>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {step === 'consent' ? (
        <>
          <div className="space-y-3 text-sm">
            <p
              className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3"
              data-testid="custom-install-warning"
            >
              {t('settings.extensions.store.warning')}
            </p>
            {!entry.compatible && (
              <p
                role="alert"
                className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-destructive"
              >
                {t('pluginStore.card.incompatible')}
              </p>
            )}
            <PermissionList declared={entry.permissions} />
            <ConsentWarnings
              permissions={entry.permissions}
              manifest={{ privacy: entry.privacy }}
            />
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-1"
                checked={accepted}
                onChange={(e) => setAccepted(e.target.checked)}
              />
              <span>{t('settings.extensions.store.accept')}</span>
            </label>
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={onClose} disabled={busy}>
              {t('settings.extensions.cancel')}
            </Button>
            {hasForm ? (
              <Button onClick={() => setStep('config')} disabled={!accepted || busy}>
                {t('pluginStore.install.next')}
              </Button>
            ) : (
              <Button onClick={() => void install().catch(() => {})} disabled={!accepted || busy}>
                {t('settings.extensions.add.install')}
              </Button>
            )}
          </DialogFooter>
        </>
      ) : (
        <>
          <SchemaForm
            idPrefix={`install-${entry.plugin_id}`}
            schema={entry.config_schema}
            resolver={resolver}
            onSubmit={install}
            submitLabel={t('settings.extensions.add.install')}
            errors={fieldErrors}
            disabled={busy}
          />
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setStep('consent')} disabled={busy}>
              {t('settings.extensions.add.back')}
            </Button>
            <Button variant="outline" onClick={onClose} disabled={busy}>
              {t('settings.extensions.cancel')}
            </Button>
          </DialogFooter>
        </>
      )}
    </>
  );
}
