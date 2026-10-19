import { ArrowDown, ArrowUp, ChevronRight, Plus } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { Button } from '../../ui/button';
import { toast } from '../../ui/sonner';
import { InstanceBadges } from './InstanceBadges';
import { displayName, enableBlock, sortInstances } from './pluginsShared';
import type { PluginInstance } from './pluginsTypes';

export function InstalledTab({
  instances,
  locked,
  onOpen,
  onAdd,
  onCreateLocal,
  onChanged,
  onReordered,
}: {
  instances: PluginInstance[];
  locked: boolean;
  onOpen: (instance: PluginInstance) => void;
  onAdd: () => void;
  onCreateLocal: () => void;
  onChanged: (instance: PluginInstance) => void;
  onReordered: () => void;
}) {
  const { t } = useTranslation();
  const [busyId, setBusyId] = useState<string | null>(null);
  const sorted = sortInstances(instances);

  const toggle = async (instance: PluginInstance, enabled: boolean) => {
    // Defence in depth: the switch is disabled for these, and the API refuses them too.
    if (enabled && (locked || enableBlock(instance) !== null)) return;
    setBusyId(instance.instance_id);
    try {
      onChanged(await api.patchPlugin(instance.instance_id, { enabled }));
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setBusyId(null);
    }
  };

  const move = async (index: number, delta: -1 | 1) => {
    const order = sorted.map((i) => i.instance_id);
    const target = index + delta;
    [order[index], order[target]] = [order[target], order[index]];
    setBusyId(sorted[index].instance_id);
    try {
      // Rewrite every position that changed so equal `sort_order`s cannot tie.
      for (const [position, id] of order.entries()) {
        const current = sorted.find((i) => i.instance_id === id);
        if (current && current.sort_order !== position) {
          await api.patchPlugin(id, { sort_order: position });
        }
      }
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      // A failure mid-way leaves a half-applied order: always show what the server holds.
      onReordered();
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap justify-end gap-2">
        <Button size="sm" variant="outline" onClick={onCreateLocal} disabled={locked}>
          <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
          {t('settings.extensions.local.newButton')}
        </Button>
        <Button size="sm" onClick={onAdd} disabled={locked}>
          <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
          {t('settings.extensions.add.button')}
        </Button>
      </div>
      {sorted.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.extensions.empty')}</p>
      )}
      <ul className="space-y-2">
        {sorted.map((instance, index) => {
          const block = enableBlock(instance);
          const busy = busyId === instance.instance_id;
          const name = displayName(instance);
          return (
            <li key={instance.instance_id} className="rounded-md border border-border p-3">
              <div className="flex items-start gap-2">
                <button
                  type="button"
                  className="min-w-0 flex-1 space-y-1 text-left"
                  onClick={() => onOpen(instance)}
                  aria-label={t('settings.extensions.openDetails', { name })}
                >
                  <div className="font-medium break-words">{name}</div>
                  <InstanceBadges instance={instance} />
                  {instance.last_error && (
                    <div className="text-xs break-words text-destructive">
                      {instance.last_error}
                    </div>
                  )}
                </button>
                <ChevronRight
                  className="mt-1 h-4 w-4 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={instance.enabled}
                    disabled={locked || busy || (block !== null && !instance.enabled)}
                    aria-label={t('settings.extensions.enableToggle', { name })}
                    onChange={(e) => void toggle(instance, e.target.checked)}
                  />
                  <span>{t('settings.extensions.enabled')}</span>
                </label>
                {block && !instance.enabled && (
                  <span className="text-xs text-muted-foreground">
                    {t(`settings.extensions.enableBlocked.${block}`)}
                  </span>
                )}
                <span className="ml-auto flex gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-9 w-9"
                    disabled={index === 0 || busyId !== null}
                    aria-label={t('settings.extensions.moveUp', { name })}
                    onClick={() => void move(index, -1)}
                  >
                    <ArrowUp className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-9 w-9"
                    disabled={index === sorted.length - 1 || busyId !== null}
                    aria-label={t('settings.extensions.moveDown', { name })}
                    onClick={() => void move(index, 1)}
                  >
                    <ArrowDown className="h-4 w-4" />
                  </Button>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
