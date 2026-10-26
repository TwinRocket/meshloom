import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell } from 'lucide-react';

import { useConversationNotifications } from '../hooks/useConversationNotifications';
import { toggledChannels } from './settings/notifications/rulesShared';
import {
  WATCHED_EXCLUDED_CHANNEL,
  WATCHED_MAX_NODES,
  findWatched,
  newWatched,
  withWatched,
  withoutWatched,
} from './settings/notifications/watchedShared';
import { Checkbox } from './ui/checkbox';

/**
 * "Notify when heard" in the contact sheet (MSHL-129, administrators): the node's adverts and
 * direct messages notify the chosen channels (`contact.node_seen`, and `contact.node_back`
 * after a long silence). Reads and writes the shared notification cache; the cooldown and the
 * silence threshold are edited in Settings > Notifications.
 */
export function WatchedNodeToggle({ publicKey }: { publicKey: string }) {
  const { t } = useTranslation();
  const { channels, rules, error, load, setWatchedNodes } = useConversationNotifications();
  useEffect(() => {
    void load(true);
  }, [load]);

  if (!rules) {
    return error ? (
      <p role="alert" className="text-[0.8125rem] text-destructive">
        {t('contactInfo.watch.loadFailed')}
      </p>
    ) : null;
  }
  const nodes = rules.watched_nodes ?? [];
  const entry = findWatched(nodes, publicKey);
  const targets = channels.filter((c) => c.id !== WATCHED_EXCLUDED_CHANNEL);
  const full = !entry && nodes.length >= WATCHED_MAX_NODES;
  const id = `watch-${publicKey.slice(0, 12)}`;

  const toggle = (on: boolean) => {
    void setWatchedNodes(
      on
        ? withWatched(
            nodes,
            newWatched(
              publicKey,
              targets.map((c) => c.id)
            )
          )
        : withoutWatched(nodes, publicKey)
    );
  };

  return (
    <div className="space-y-2" data-testid="watched-node-toggle">
      <label htmlFor={id} className="flex cursor-pointer items-center gap-2 text-sm">
        <Checkbox
          id={id}
          checked={!!entry}
          disabled={full}
          onCheckedChange={(checked) => toggle(checked === true)}
        />
        <Bell className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <span>{t('contactInfo.watch.toggle')}</span>
      </label>
      <p className="text-[0.75rem] text-muted-foreground">
        {full
          ? t('contactInfo.watch.full', { max: WATCHED_MAX_NODES })
          : t('contactInfo.watch.help')}
      </p>
      {entry && (
        <div className="space-y-1">
          <div
            className="flex flex-wrap gap-1.5"
            role="group"
            aria-label={t('contactInfo.watch.channels')}
          >
            {targets.map((channel) => {
              const on = entry.channels.includes(channel.id);
              return (
                <button
                  key={channel.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    void setWatchedNodes(
                      withWatched(nodes, {
                        ...entry,
                        channels: toggledChannels(entry.channels, channel.id, !on),
                      })
                    )
                  }
                  className={`rounded-full border px-2.5 py-0.5 text-[0.75rem] ${
                    on
                      ? 'border-primary bg-primary/10 text-foreground'
                      : 'border-border text-muted-foreground'
                  }`}
                >
                  {channel.name}
                </button>
              );
            })}
          </div>
          {entry.channels.length === 0 && (
            <p className="text-[0.75rem] text-muted-foreground">
              {t('contactInfo.watch.noChannel')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
