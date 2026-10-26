import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Pencil, Plus, Trash2 } from 'lucide-react';

import type {
  NotificationChannel,
  NotificationKeywordInput,
  NotificationKeywordRule,
} from '../../../notifications/types';
import type { Channel, Contact } from '../../../types';
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
import {
  KEYWORD_EXCLUDED_CHANNEL,
  KEYWORD_MAX_COOLDOWN_S,
  KEYWORD_MAX_PATTERN,
  KEYWORD_MAX_RULES,
  draftError,
  draftOf,
  emptyKeywordDraft,
  withDraft,
  withoutRule,
  type KeywordDraft,
} from './keywordsShared';
import { channelLabel, toggledChannels } from './rulesShared';

const SELECT_CLASS =
  'h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

/**
 * Settings > Notifications, Keywords (MSHL-128): rules that notify a channel when a message
 * holds a word or phrase (no regex). The list is saved whole (`PATCH /rules`, `keywords`); the
 * server enforces the limits again.
 */
export function KeywordRulesCard({
  rules,
  channels,
  contacts,
  chatChannels,
  disabled,
  onSave,
}: {
  rules: NotificationKeywordRule[];
  channels: NotificationChannel[];
  contacts: Contact[];
  chatChannels: Channel[];
  disabled?: boolean;
  onSave: (next: NotificationKeywordInput[]) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<KeywordDraft | null>(null);
  const full = rules.length >= KEYWORD_MAX_RULES;
  const targets = channels.filter((c) => c.id !== KEYWORD_EXCLUDED_CHANNEL);

  const scopeText = (rule: NotificationKeywordRule): string => {
    if (rule.scope === 'all') return t('settings.notifications.keywords.scopeAll');
    if (rule.scope === 'dms') return t('settings.notifications.keywords.scopeDms');
    return rule.scope.map((k) => conversationName(k, contacts, chatChannels)).join(', ');
  };

  return (
    <div className="space-y-3" data-testid="keyword-rules">
      <p
        className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-[0.8125rem] text-warning"
        role="note"
        data-testid="keyword-forgery-warning"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{t('settings.notifications.keywords.forgery')}</span>
      </p>
      {rules.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.keywords.empty')}
        </p>
      ) : (
        <ul className="space-y-2">
          {rules.map((rule) => (
            <li
              key={rule.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border/60 px-3 py-2"
            >
              <span className="min-w-0 break-words text-sm font-medium">“{rule.pattern}”</span>
              <span className="text-[0.75rem] text-muted-foreground">
                {t(`settings.notifications.keywords.match_${rule.match}`)} · {scopeText(rule)} ·{' '}
                {rule.channels.length === 0
                  ? t('settings.notifications.keywords.noChannel')
                  : rule.channels.map((id) => channelLabel(channels, id)).join(', ')}
              </span>
              <span className="ml-auto flex gap-1">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => setEditing(draftOf(rule))}
                  className="rounded-full p-1 transition-colors hover:bg-accent disabled:opacity-50"
                  aria-label={t('settings.notifications.keywords.edit', { pattern: rule.pattern })}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onSave(withoutRule(rules, rule.id))}
                  className="rounded-full p-1 transition-colors hover:bg-accent disabled:opacity-50"
                  aria-label={t('settings.notifications.keywords.remove', {
                    pattern: rule.pattern,
                  })}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || full}
          onClick={() => setEditing(emptyKeywordDraft())}
        >
          <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
          {t('settings.notifications.keywords.add')}
        </Button>
        <span className="text-[0.75rem] text-muted-foreground">
          {full
            ? t('settings.notifications.keywords.full', { max: KEYWORD_MAX_RULES })
            : t('settings.notifications.keywords.count', {
                count: rules.length,
                max: KEYWORD_MAX_RULES,
              })}
        </span>
      </div>
      {editing && (
        <KeywordDialog
          initial={editing}
          channels={targets}
          contacts={contacts}
          chatChannels={chatChannels}
          onClose={() => setEditing(null)}
          onSave={(draft) => {
            onSave(withDraft(rules, draft));
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function KeywordDialog({
  initial,
  channels,
  contacts,
  chatChannels,
  onClose,
  onSave,
}: {
  initial: KeywordDraft;
  channels: NotificationChannel[];
  contacts: Contact[];
  chatChannels: Channel[];
  onClose: () => void;
  onSave: (draft: KeywordDraft) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(initial);
  const [filter, setFilter] = useState('');
  const set = (next: Partial<KeywordDraft>) => setDraft((d) => ({ ...d, ...next }));
  const error = draftError(draft);

  // The conversation picker: every direct and channel conversation the UI knows, by name.
  const conversations = useMemo(() => {
    const all = [
      ...contacts.map((c) => `contact-${c.public_key}`),
      ...chatChannels.map((c) => `channel-${c.key}`),
    ];
    for (const key of draft.scopeKeys) if (!all.includes(key)) all.push(key);
    return all.map((key) => ({ key, name: conversationName(key, contacts, chatChannels) }));
  }, [contacts, chatChannels, draft.scopeKeys]);
  const shown = conversations.filter((c) =>
    c.name.toLowerCase().includes(filter.trim().toLowerCase())
  );

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90dvh] w-[calc(100vw-1.5rem)] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {initial.id
              ? t('settings.notifications.keywords.editTitle')
              : t('settings.notifications.keywords.addTitle')}
          </DialogTitle>
          <DialogDescription>{t('settings.notifications.keywords.dialogHelp')}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!error) onSave(draft);
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="keyword-pattern">{t('settings.notifications.keywords.pattern')}</Label>
            <Input
              id="keyword-pattern"
              value={draft.pattern}
              maxLength={KEYWORD_MAX_PATTERN}
              autoComplete="off"
              onChange={(e) => set({ pattern: e.target.value })}
            />
            <p className="text-[0.75rem] text-muted-foreground">
              {t('settings.notifications.keywords.patternHelp', { max: KEYWORD_MAX_PATTERN })}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="keyword-match">{t('settings.notifications.keywords.match')}</Label>
            <select
              id="keyword-match"
              value={draft.match}
              onChange={(e) => set({ match: e.target.value as KeywordDraft['match'] })}
              className={SELECT_CLASS}
            >
              <option value="contains">
                {t('settings.notifications.keywords.match_contains')}
              </option>
              <option value="word">{t('settings.notifications.keywords.match_word')}</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="keyword-scope">{t('settings.notifications.keywords.scope')}</Label>
            <select
              id="keyword-scope"
              value={draft.scopeKind}
              onChange={(e) => set({ scopeKind: e.target.value as KeywordDraft['scopeKind'] })}
              className={SELECT_CLASS}
            >
              <option value="all">{t('settings.notifications.keywords.scopeAll')}</option>
              <option value="dms">{t('settings.notifications.keywords.scopeDms')}</option>
              <option value="chosen">{t('settings.notifications.keywords.scopeChosen')}</option>
            </select>
            {draft.scopeKind === 'chosen' && (
              <div className="space-y-1.5" data-testid="keyword-conversations">
                <Input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  aria-label={t('settings.notifications.keywords.scopeFilter')}
                  placeholder={t('settings.notifications.keywords.scopeFilter')}
                />
                <ul className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-border/60 p-2">
                  {shown.map((c) => (
                    <li key={c.key}>
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={draft.scopeKeys.includes(c.key)}
                          onChange={(e) =>
                            set({
                              scopeKeys: toggledChannels(draft.scopeKeys, c.key, e.target.checked),
                            })
                          }
                        />
                        <span className="break-words">{c.name}</span>
                      </label>
                    </li>
                  ))}
                </ul>
                {draft.scopeKeys.length === 0 && (
                  <p className="text-[0.75rem] text-destructive">
                    {t('settings.notifications.keywords.scopeNone')}
                  </p>
                )}
              </div>
            )}
          </div>
          <div className="space-y-1.5">
            <Label>{t('settings.notifications.keywords.channels')}</Label>
            <div className="flex flex-wrap gap-1.5">
              {channels.map((channel) => {
                const on = draft.channels.includes(channel.id);
                return (
                  <button
                    key={channel.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() =>
                      set({ channels: toggledChannels(draft.channels, channel.id, !on) })
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
            <p className="text-[0.75rem] text-muted-foreground">
              {t('settings.notifications.keywords.channelsHelp')}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="keyword-cooldown">
              {t('settings.notifications.keywords.cooldown')}
            </Label>
            <Input
              id="keyword-cooldown"
              type="number"
              min={0}
              max={KEYWORD_MAX_COOLDOWN_S / 60}
              step={1}
              value={Number.isNaN(draft.cooldownMin) ? '' : draft.cooldownMin}
              onChange={(e) =>
                set({ cooldownMin: e.target.value === '' ? NaN : Number(e.target.value) })
              }
            />
            <p className="text-[0.75rem] text-muted-foreground">
              {t('settings.notifications.keywords.cooldownHelp')}
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('settings.notifications.keywords.cancel')}
            </Button>
            <Button type="submit" disabled={error !== null}>
              {t('settings.notifications.keywords.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
