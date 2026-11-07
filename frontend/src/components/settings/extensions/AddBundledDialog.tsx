import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { Button } from '../../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../ui/dialog';
import { toast } from '../../ui/sonner';
import { ConsentWarnings, PermissionList } from './PermissionList';
import { localizedText } from './pluginsShared';
import type { BundledPlugin, PluginInstance } from './pluginsTypes';

/**
 * "Add a bundled plugin": pick one from `GET /plugins/bundled`, read the consent
 * (permissions in plain words, hosts, privacy, the exfiltration sentence), accept it
 * and install. The grant sent is exactly what the manifest declares: the API refuses a
 * narrower one at install.
 */
export function AddBundledDialog({
  open,
  onOpenChange,
  onInstalled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInstalled: (instance: PluginInstance) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto">
        <AddBody onOpenChange={onOpenChange} onInstalled={onInstalled} />
      </DialogContent>
    </Dialog>
  );
}

/** Mounted only while the dialog is open, so every opening starts from the list. */
function AddBody({
  onOpenChange,
  onInstalled,
}: {
  onOpenChange: (open: boolean) => void;
  onInstalled: (instance: PluginInstance) => void;
}) {
  const { t, i18n } = useTranslation();
  const [bundled, setBundled] = useState<BundledPlugin[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<BundledPlugin | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getBundledPlugins()
      .then((rows) => alive && setBundled(rows))
      .catch((err) => alive && setError(formatApiError(err, t)));
    return () => {
      alive = false;
    };
  }, [t]);

  const install = async () => {
    if (!chosen) return;
    setBusy(true);
    try {
      const instance = await api.installPlugin({
        kind: 'bundled',
        plugin_id: chosen.plugin_id,
        granted_permissions: chosen.permissions,
      });
      toast.success(t('settings.extensions.add.installed', { name: instance.label }));
      onInstalled(instance);
      onOpenChange(false);
    } catch (err) {
      setError(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const name = (p: BundledPlugin) => localizedText(p.name, i18n.language) || p.plugin_id;

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {chosen
            ? t('settings.extensions.add.consentTitle', { name: name(chosen) })
            : t('settings.extensions.add.title')}
        </DialogTitle>
        <DialogDescription>
          {chosen ? t('settings.extensions.add.consentIntro') : t('settings.extensions.add.intro')}
        </DialogDescription>
      </DialogHeader>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {!chosen && bundled === null && !error && (
        <p className="text-sm text-muted-foreground">{t('settings.extensions.loading')}</p>
      )}
      {!chosen && bundled?.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.extensions.add.none')}</p>
      )}
      {!chosen && bundled && bundled.length > 0 && (
        <ul className="space-y-2">
          {bundled.map((p) => (
            <li key={p.plugin_id}>
              <button
                type="button"
                className="w-full rounded-md border border-border p-3 text-left hover:bg-accent"
                onClick={() => setChosen(p)}
              >
                <div className="font-medium break-words">{name(p)}</div>
                <div className="text-sm text-muted-foreground break-words">
                  {localizedText(p.summary, i18n.language)}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  v{p.version}
                  {p.instances > 0 &&
                    ` · ${t('settings.extensions.add.instances', { count: p.instances })}`}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {chosen && (
        <div className="space-y-3">
          <PermissionList declared={chosen.permissions} />
          <ConsentWarnings
            permissions={chosen.permissions}
            manifest={{ privacy: chosen.privacy }}
          />
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={accepted}
              onChange={(e) => setAccepted(e.target.checked)}
            />
            <span>{t('settings.extensions.add.accept')}</span>
          </label>
        </div>
      )}

      <DialogFooter className="gap-2">
        {chosen && (
          <Button variant="outline" onClick={() => setChosen(null)} disabled={busy}>
            {t('settings.extensions.add.back')}
          </Button>
        )}
        <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
          {t('settings.extensions.cancel')}
        </Button>
        {chosen && (
          <Button onClick={install} disabled={!accepted || busy}>
            {t('settings.extensions.add.install')}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
