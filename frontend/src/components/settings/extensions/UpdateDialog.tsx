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
import { localizedText, permissionKey } from './pluginsShared';
import type { PluginInstance, PluginUpdate, StoreRepository } from './pluginsTypes';

const SECRET_MASK = '********';

/** A permission's title in plain words, falling back to its registry name. */
function PermissionNames({ names }: { names: string[] }) {
  const { t } = useTranslation();
  return (
    <>
      {names.map((name, i) => (
        <span key={name}>
          {i > 0 && ', '}
          {t(`${permissionKey(name)}.title`, { defaultValue: name })}
        </span>
      ))}
    </>
  );
}

/**
 * The update of one plugin: version, changelog (when the source has one), what the new
 * release changes in permissions and settings, then the consent and the settings the
 * backend holds an instance for. The release data (permissions, config schema) comes from
 * the repository entry the Store cached: the same data `GET /store/updates` diffed. The
 * consent screen lists every permission of that release, and the grant sent is exactly
 * that list. The update is shared by every instance of the plugin; the consent and the
 * settings given here apply to one instance (the first one that is held), the others
 * wait for their own review under Installed.
 */
export function UpdateDialogBody({
  update,
  repo,
  instances,
  onClose,
  onUpdated,
}: {
  update: PluginUpdate;
  repo: StoreRepository | undefined;
  instances: PluginInstance[];
  onClose: () => void;
  onUpdated: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [step, setStep] = useState<'review' | 'config'>('review');
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const resolver = useMemo(() => languageMapResolver(i18n.language), [i18n.language]);

  const entry = repo?.entry && repo.entry.version === update.to_version ? repo.entry : null;
  const carrier =
    update.instances.find((i) => i.hold === 'consent_required') ??
    update.instances.find((i) => i.hold === 'config_required') ??
    update.instances[0];
  const carrierInstance = instances.find((i) => i.instance_id === carrier?.instance_id);
  const needsConsent = update.instances.some((i) => i.hold === 'consent_required');
  const needsConfig = update.instances.some((i) => i.hold === 'config_required');
  // The consent can only be given when the exact release is known (and so listed).
  const canConsent = entry !== null;
  const showConfig = canConsent && schemaHasFields(entry.config_schema) && needsConfig;
  const name = localizedText(update.name, i18n.language) || update.plugin_id;

  const secretsSet = useMemo(
    () =>
      carrierInstance
        ? carrierInstance.secret_fields.filter((f) => carrierInstance.config[f] === SECRET_MASK)
        : [],
    [carrierInstance]
  );

  const run = async (config?: Record<string, unknown>) => {
    if (!carrier) return;
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await api.updatePlugin(carrier.instance_id, {
        version: update.to_version,
        // The grant is the list shown above, never more; only given when the backend
        // asked for a consent (otherwise the instance keeps what it has).
        ...(needsConsent && entry ? { granted_permissions: entry.permissions } : {}),
        ...(config ? { config } : {}),
      });
      toast.success(t('pluginStore.updates.updated', { name, version: update.to_version }));
      onUpdated();
    } catch (err) {
      const issues = configIssueErrors(err, t);
      if (issues) setFieldErrors(issues);
      else setError(formatApiError(err, t));
      throw err;
    } finally {
      setBusy(false);
    }
  };

  const consentNeeded = needsConsent && canConsent;
  // A consent the screen cannot list (the Store no longer holds the target release) is not
  // asked blindly: the update waits until the Store is refreshed.
  const consentUnavailable = needsConsent && !canConsent;
  const ready = !consentUnavailable && (!consentNeeded || accepted);

  return (
    <>
      <DialogHeader>
        <DialogTitle className="break-words">
          {t('pluginStore.updates.dialogTitle', { name })}
        </DialogTitle>
        <DialogDescription>
          {t('pluginStore.updates.dialogIntro', {
            from: update.from_version,
            to: update.to_version,
          })}
        </DialogDescription>
      </DialogHeader>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {step === 'review' ? (
        <>
          <div className="space-y-3 text-sm" data-testid="update-review">
            <section>
              <h3 className="font-medium">{t('pluginStore.updates.changelog')}</h3>
              {update.changelog ? (
                <pre
                  data-testid="update-changelog"
                  className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-2 font-mono text-xs"
                >
                  {update.changelog}
                </pre>
              ) : (
                <p className="text-muted-foreground">{t('pluginStore.updates.noChangelog')}</p>
              )}
            </section>
            <section className="space-y-1" data-testid="update-permission-diff">
              <h3 className="font-medium">{t('pluginStore.updates.permissionsTitle')}</h3>
              {update.permissions.added.length +
                update.permissions.changed.length +
                update.permissions.removed.length ===
                0 && !update.permissions.privacy_widened ? (
                <p className="text-muted-foreground">
                  {t('pluginStore.updates.noPermissionChange')}
                </p>
              ) : (
                <ul className="list-disc pl-5">
                  {update.permissions.added.length > 0 && (
                    <li>
                      {t('pluginStore.updates.permAdded')}{' '}
                      <PermissionNames names={update.permissions.added} />
                    </li>
                  )}
                  {update.permissions.changed.length > 0 && (
                    <li>
                      {t('pluginStore.updates.permChanged')}{' '}
                      <PermissionNames names={update.permissions.changed} />
                    </li>
                  )}
                  {update.permissions.removed.length > 0 && (
                    <li>
                      {t('pluginStore.updates.permRemoved')}{' '}
                      <PermissionNames names={update.permissions.removed} />
                    </li>
                  )}
                  {update.permissions.privacy_widened && (
                    <li>{t('pluginStore.updates.privacyWidened')}</li>
                  )}
                </ul>
              )}
            </section>
            <section className="space-y-1" data-testid="update-schema-diff">
              <h3 className="font-medium">{t('pluginStore.updates.settingsTitle')}</h3>
              {update.config_schema === null ? (
                <p className="text-muted-foreground">{t('pluginStore.updates.schemaUnknown')}</p>
              ) : update.config_schema.added.length +
                  update.config_schema.removed.length +
                  update.config_schema.required_added.length ===
                0 ? (
                <p className="text-muted-foreground">{t('pluginStore.updates.noSettingChange')}</p>
              ) : (
                <ul className="list-disc pl-5">
                  {update.config_schema.added.length > 0 && (
                    <li className="break-words">
                      {t('pluginStore.updates.fieldsAdded', {
                        fields: update.config_schema.added.join(', '),
                      })}
                    </li>
                  )}
                  {update.config_schema.required_added.length > 0 && (
                    <li className="break-words">
                      {t('pluginStore.updates.fieldsRequired', {
                        fields: update.config_schema.required_added.join(', '),
                      })}
                    </li>
                  )}
                  {update.config_schema.removed.length > 0 && (
                    <li className="break-words">
                      {t('pluginStore.updates.fieldsRemoved', {
                        fields: update.config_schema.removed.join(', '),
                      })}
                    </li>
                  )}
                </ul>
              )}
            </section>
            {update.instances.length > 1 && (
              <p className="text-muted-foreground">{t('pluginStore.updates.sharedNote')}</p>
            )}
            {consentUnavailable && (
              <p role="alert" className="text-destructive" data-testid="update-consent-unavailable">
                {t('pluginStore.updates.consentUnavailable')}
              </p>
            )}
            {needsConfig && !needsConsent && !canConsent && (
              <p role="status" className="text-muted-foreground">
                {t('pluginStore.updates.reviewLater')}
              </p>
            )}
            {consentNeeded && entry && (
              <section className="space-y-2" data-testid="update-consent">
                <h3 className="font-medium">{t('pluginStore.updates.consentTitle')}</h3>
                <p className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
                  {t('settings.extensions.store.warning')}
                </p>
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
              </section>
            )}
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={onClose} disabled={busy}>
              {t('settings.extensions.cancel')}
            </Button>
            {showConfig ? (
              <Button onClick={() => setStep('config')} disabled={!ready || busy}>
                {t('pluginStore.install.next')}
              </Button>
            ) : (
              <Button onClick={() => void run().catch(() => {})} disabled={!ready || busy}>
                {t('pluginStore.updates.update')}
              </Button>
            )}
          </DialogFooter>
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">{t('pluginStore.updates.configIntro')}</p>
          <SchemaForm
            idPrefix={`update-${update.plugin_id}`}
            schema={entry?.config_schema ?? {}}
            values={carrierInstance?.config}
            secretsSet={secretsSet}
            resolver={resolver}
            onSubmit={run}
            submitLabel={t('pluginStore.updates.update')}
            errors={fieldErrors}
            disabled={busy}
            flagged={carrier?.missing_fields ?? []}
          />
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setStep('review')} disabled={busy}>
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
