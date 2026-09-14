import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronUp, ChevronDown, Check, Plus } from 'lucide-react';
import { DEFAULT_RAIL, RAIL_ITEMS, resolveRail, type RailItemId } from '../navDestinations';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';
import { Button } from '../ui/button';
import { cn } from '../../lib/utils';

/**
 * What the desktop rail holds, and in what order.
 *
 * One ordered list rather than a set of pinned things plus an order for them: two
 * notions can disagree, one list cannot. Adding and reordering are therefore the
 * same edit, done in one place.
 *
 * Up and down buttons rather than dragging. Dragging needs a keyboard equivalent
 * to be usable at all, it is awkward on 40px targets, and it is a great deal more
 * code for a list that is arranged once.
 */

interface Props {
  order: string[];
  onChange: (order: RailItemId[]) => void;
}

export function SettingsNavigationSection({ order, onChange }: Props) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState(false);

  const current = useMemo(() => resolveRail(order).map((item) => item.id), [order]);
  const available = useMemo(
    () => RAIL_ITEMS.filter((item) => !current.includes(item.id)),
    [current]
  );

  const commit = (next: RailItemId[]) => {
    onChange(next);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1500);
  };

  const move = (index: number, delta: number) => {
    const next = [...current];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    commit(next);
  };

  return (
    <div className="space-y-4">
      <SettingsGroup id="settings-navigation">
        <SettingsGroupHeader
          id="settings-navigation"
          title={t('settingsNavigation.railTitle')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">{t('settingsNavigation.railHelp')}</p>

        <ul className="overflow-hidden rounded-xl border border-border/60">
          {current.map((id, index) => {
            const item = RAIL_ITEMS.find((candidate) => candidate.id === id);
            if (!item) return null;
            const { Icon, labelKey, permanent } = item;
            return (
              <li
                key={id}
                className={cn(
                  'flex items-center gap-3 px-3 py-2',
                  index > 0 && 'border-t border-border/40'
                )}
              >
                <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate text-sm">{t(labelKey)}</span>
                {permanent && (
                  <span className="shrink-0 text-[0.6875rem] uppercase tracking-wider text-muted-foreground">
                    {t('settingsNavigation.permanent')}
                  </span>
                )}
                <span className="flex shrink-0 items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={index === 0}
                    onClick={() => move(index, -1)}
                    aria-label={t('settingsNavigation.moveUp', { name: t(labelKey) })}
                  >
                    <ChevronUp className="h-4 w-4" aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={index === current.length - 1}
                    onClick={() => move(index, 1)}
                    aria-label={t('settingsNavigation.moveDown', { name: t(labelKey) })}
                  >
                    <ChevronDown className="h-4 w-4" aria-hidden="true" />
                  </Button>
                  {/* A permanent entry has no remove control at all, rather than a
                      disabled one: it is not a thing you may do later. */}
                  {!permanent && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => commit(current.filter((candidate) => candidate !== id))}
                      aria-label={t('settingsNavigation.remove', { name: t(labelKey) })}
                    >
                      {t('settingsNavigation.removeShort')}
                    </Button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>

        {available.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
              {t('settingsNavigation.available')}
            </h4>
            <div className="flex flex-wrap gap-2">
              {available.map(({ id, labelKey, Icon }) => (
                <Button
                  key={id}
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => commit([...current, id])}
                >
                  <Icon className="mr-1.5 h-4 w-4" aria-hidden="true" />
                  <Plus className="mr-1 h-3 w-3" aria-hidden="true" />
                  {t(labelKey)}
                </Button>
              ))}
            </div>
          </div>
        )}

        <div className="flex items-center gap-3">
          <Button type="button" variant="outline" size="sm" onClick={() => commit(DEFAULT_RAIL)}>
            {t('settingsNavigation.reset')}
          </Button>
          {saved && (
            <span className="flex items-center gap-1 text-[0.8125rem] text-success" role="status">
              <Check className="h-3.5 w-3.5" aria-hidden="true" />
              {t('settingsNavigation.saved')}
            </span>
          )}
        </div>
      </SettingsGroup>
    </div>
  );
}
