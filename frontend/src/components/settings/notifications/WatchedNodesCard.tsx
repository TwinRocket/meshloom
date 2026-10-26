import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pencil, Trash2 } from 'lucide-react';

import type { NotificationChannel, NotificationWatchedNode } from '../../../notifications/types';
import type { Contact } from '../../../types';
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
import { conversationName } from './ConversationExceptions';
import { channelLabel, toggledChannels } from './rulesShared';
import {
  WATCHED_EXCLUDED_CHANNEL,
  WATCHED_MAX_COOLDOWN_S,
  WATCHED_MIN_COOLDOWN_S,
  WATCHED_MAX_NODES,
  WATCHED_MAX_SILENCE_S,
  watchedError,
  withWatched,
  withoutWatched,
} from './watchedShared';

/**
 * Settings > Notifications, Watched nodes (MSHL-129): the nodes that notify when they are heard
 * (`contact.node_seen`) or come back after a silence (`contact.node_back`). A node is added from
 * its contact sheet; this list edits the channels, the cooldown and the silence threshold, and
 * removes. The list is saved whole (`PATCH /rules`, `watched_nodes`).
 */
export function WatchedNodesCard({
  nodes,
  channels,
  contacts,
  disabled,
  onSave,
}: {
  nodes: NotificationWatchedNode[];
  channels: NotificationChannel[];
  contacts: Contact[];
  disabled?: boolean;
  onSave: (next: NotificationWatchedNode[]) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<NotificationWatchedNode | null>(null);
  const targets = channels.filter((c) => c.id !== WATCHED_EXCLUDED_CHANNEL);
  const label = (node: NotificationWatchedNode) =>
    conversationName(`contact-${node.public_key}`, contacts, []);

  return (
    <div className="space-y-3" data-testid="watched-nodes">
      {nodes.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.watched.empty')}
        </p>
      ) : (
        <ul className="space-y-2">
          {nodes.map((node) => (
            <li
              key={node.public_key}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border/60 px-3 py-2"
            >
              <span className="min-w-0 break-words text-sm font-medium">{label(node)}</span>
              <span className="text-[0.75rem] text-muted-foreground">
                {node.channels.length === 0
                  ? t('settings.notifications.watched.noChannel')
                  : node.channels.map((id) => channelLabel(channels, id)).join(', ')}{' '}
                ·{' '}
                {t('settings.notifications.watched.summary', {
                  cooldown: Math.round(node.cooldown_s / 60),
                  silence: Math.round(node.silence_s / 3600),
                })}
              </span>
              <span className="ml-auto flex gap-1">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => setEditing(node)}
                  className="rounded-full p-1 transition-colors hover:bg-accent disabled:opacity-50"
                  aria-label={t('settings.notifications.watched.edit', { name: label(node) })}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onSave(withoutWatched(nodes, node.public_key))}
                  className="rounded-full p-1 transition-colors hover:bg-accent disabled:opacity-50"
                  aria-label={t('settings.notifications.watched.remove', { name: label(node) })}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[0.75rem] text-muted-foreground">
        {nodes.length >= WATCHED_MAX_NODES
          ? t('settings.notifications.watched.full', { max: WATCHED_MAX_NODES })
          : t('settings.notifications.watched.count', {
              count: nodes.length,
              max: WATCHED_MAX_NODES,
            })}
      </p>
      {editing && (
        <WatchedDialog
          initial={editing}
          name={label(editing)}
          channels={targets}
          onClose={() => setEditing(null)}
          onSave={(node) => {
            onSave(withWatched(nodes, node));
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function WatchedDialog({
  initial,
  name,
  channels,
  onClose,
  onSave,
}: {
  initial: NotificationWatchedNode;
  name: string;
  channels: NotificationChannel[];
  onClose: () => void;
  onSave: (node: NotificationWatchedNode) => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState(initial.channels);
  const [cooldownMin, setCooldownMin] = useState(Math.round(initial.cooldown_s / 60));
  const [silenceH, setSilenceH] = useState(Math.round(initial.silence_s / 3600));
  const error = watchedError(cooldownMin, silenceH);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90dvh] w-[calc(100vw-1.5rem)] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('settings.notifications.watched.editTitle', { name })}</DialogTitle>
          <DialogDescription>{t('settings.notifications.watched.dialogHelp')}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!error) {
              onSave({
                ...initial,
                channels: selected,
                cooldown_s: cooldownMin * 60,
                silence_s: silenceH * 3600,
              });
            }
          }}
        >
          <div className="space-y-1.5">
            <Label>{t('settings.notifications.watched.channels')}</Label>
            <div className="flex flex-wrap gap-1.5">
              {channels.map((channel) => {
                const on = selected.includes(channel.id);
                return (
                  <button
                    key={channel.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setSelected(toggledChannels(selected, channel.id, !on))}
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
            <p className="text-[0.75rem] text-muted-foreground">
              {t('settings.notifications.watched.channelsHelp')}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="watched-cooldown">{t('settings.notifications.watched.cooldown')}</Label>
            <Input
              id="watched-cooldown"
              type="number"
              min={WATCHED_MIN_COOLDOWN_S / 60}
              max={WATCHED_MAX_COOLDOWN_S / 60}
              step={1}
              value={Number.isNaN(cooldownMin) ? '' : cooldownMin}
              onChange={(e) => setCooldownMin(e.target.value === '' ? NaN : Number(e.target.value))}
            />
            <p className="text-[0.75rem] text-muted-foreground">
              {t('settings.notifications.watched.cooldownHelp')}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="watched-silence">{t('settings.notifications.watched.silence')}</Label>
            <Input
              id="watched-silence"
              type="number"
              min={1}
              max={WATCHED_MAX_SILENCE_S / 3600}
              step={1}
              value={Number.isNaN(silenceH) ? '' : silenceH}
              onChange={(e) => setSilenceH(e.target.value === '' ? NaN : Number(e.target.value))}
            />
            <p className="text-[0.75rem] text-muted-foreground">
              {t('settings.notifications.watched.silenceHelp')}
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('settings.notifications.watched.cancel')}
            </Button>
            <Button type="submit" disabled={error !== null}>
              {t('settings.notifications.watched.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
