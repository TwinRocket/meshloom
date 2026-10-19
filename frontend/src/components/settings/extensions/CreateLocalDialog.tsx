import { useState } from 'react';
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
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { LOCAL_HOOKS, type LocalHook, type PluginInstance } from './pluginsTypes';

/** "New local plugin": a label, the events it listens to and an SDK template. */
export function CreateLocalDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (instance: PluginInstance) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] w-[calc(100%-2rem)] max-w-md overflow-y-auto">
        <CreateBody onOpenChange={onOpenChange} onCreated={onCreated} />
      </DialogContent>
    </Dialog>
  );
}

function CreateBody({
  onOpenChange,
  onCreated,
}: {
  onOpenChange: (open: boolean) => void;
  onCreated: (instance: PluginInstance) => void;
}) {
  const { t } = useTranslation();
  const [label, setLabel] = useState('');
  const [template, setTemplate] = useState<'echo' | 'blank'>('echo');
  const [hooks, setHooks] = useState<LocalHook[]>(['message']);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      // The echo template replies, so it starts with the permissions it needs.
      const permissions: Record<string, Record<string, unknown>> = template === 'echo'
        ? { 'messages.read': { channels: 'public' }, 'messages.send': { targets: 'reply' } }
        : {};
      const created = await api.createLocalPlugin({
        label: label.trim(),
        hooks,
        permissions,
        template,
      });
      onCreated(created);
      onOpenChange(false);
    } catch (err) {
      setError(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t('settings.extensions.local.create.title')}</DialogTitle>
        <DialogDescription>{t('settings.extensions.local.create.text')}</DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <div className="space-y-1">
          <Label htmlFor="local-plugin-label">{t('settings.extensions.local.create.label')}</Label>
          <Input
            id="local-plugin-label"
            value={label}
            maxLength={80}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="local-plugin-template">
            {t('settings.extensions.local.create.template')}
          </Label>
          <select
            id="local-plugin-template"
            className="h-10 w-full rounded-md border border-input bg-background px-2 text-base md:text-sm"
            value={template}
            onChange={(e) => setTemplate(e.target.value as 'echo' | 'blank')}
          >
            <option value="echo">{t('settings.extensions.local.create.templateEcho')}</option>
            <option value="blank">{t('settings.extensions.local.create.templateBlank')}</option>
          </select>
        </div>
        <HooksPicker value={hooks} onChange={setHooks} disabled={busy} />
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
      <DialogFooter className="gap-2">
        <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
          {t('settings.extensions.cancel')}
        </Button>
        <Button onClick={create} disabled={busy || label.trim() === '' || hooks.length === 0}>
          {t('settings.extensions.local.create.confirm')}
        </Button>
      </DialogFooter>
    </>
  );
}

/** The events a local plugin subscribes to (at least one). */
export function HooksPicker({
  value,
  onChange,
  disabled,
}: {
  value: LocalHook[];
  onChange: (hooks: LocalHook[]) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <fieldset className="space-y-1">
      <legend className="text-sm font-medium">{t('settings.extensions.local.hooks.title')}</legend>
      <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
        {LOCAL_HOOKS.map((hook) => (
          <label key={hook} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={disabled}
              checked={value.includes(hook)}
              onChange={(e) =>
                onChange(e.target.checked ? [...value, hook] : value.filter((h) => h !== hook))
              }
            />
            <span>{t(`settings.extensions.local.hooks.${hook}`)}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
