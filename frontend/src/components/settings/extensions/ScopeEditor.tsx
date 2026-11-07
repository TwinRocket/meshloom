import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../../api';
import type { Channel, Contact } from '../../../types';

type Mode = 'all' | 'none' | 'only' | 'except';
type Filter = string[] | { except: string[] };

interface MessagesScope {
  mode: Mode;
  channels: string[];
  contacts: string[];
}

function keysOf(filter: unknown): string[] {
  if (Array.isArray(filter)) return filter.filter((k): k is string => typeof k === 'string');
  if (
    filter &&
    typeof filter === 'object' &&
    Array.isArray((filter as { except?: unknown }).except)
  ) {
    return (filter as { except: unknown[] }).except.filter(
      (k): k is string => typeof k === 'string'
    );
  }
  return [];
}

/** Read the stored `scope` (`{}` = all) into the editor's model. */
export function parseScope(scope: Record<string, unknown>): MessagesScope {
  const messages = scope.messages ?? 'all';
  if (messages === 'all' || messages === 'none') {
    return { mode: messages, channels: [], contacts: [] };
  }
  const record = (messages ?? {}) as { channels?: unknown; contacts?: unknown };
  const isExcept = (f: unknown) => !!f && typeof f === 'object' && !Array.isArray(f);
  return {
    mode: isExcept(record.channels) || isExcept(record.contacts) ? 'except' : 'only',
    channels: keysOf(record.channels),
    contacts: keysOf(record.contacts),
  };
}

/** Build the `scope` the API accepts (`{messages: all | none | {channels, contacts}}`). */
export function buildScope(model: MessagesScope): Record<string, unknown> {
  if (model.mode === 'all' || model.mode === 'none') return { messages: model.mode };
  const wrap = (keys: string[]): Filter => (model.mode === 'except' ? { except: keys } : keys);
  return { messages: { channels: wrap(model.channels), contacts: wrap(model.contacts) } };
}

const MODES: Mode[] = ['all', 'none', 'only', 'except'];

/** Which conversations a plugin may read: all, none, only these, or all but these. */
export function ScopeEditor({
  scope,
  onChange,
  disabled,
}: {
  scope: Record<string, unknown>;
  onChange: (scope: Record<string, unknown>) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const model = parseScope(scope);
  const listMode = model.mode === 'only' || model.mode === 'except';

  useEffect(() => {
    if (!listMode) return;
    let alive = true;
    api
      .getChannels()
      .then((rows) => alive && setChannels(rows))
      .catch(console.error);
    api
      .getContacts(1000, 0)
      .then((rows) => alive && setContacts(rows.filter((c) => c.type === 0 || c.type === 1)))
      .catch(console.error);
    return () => {
      alive = false;
    };
  }, [listMode]);

  const toggle = (field: 'channels' | 'contacts', key: string) => {
    const current = model[field];
    const next = current.includes(key) ? current.filter((k) => k !== key) : [...current, key];
    onChange(buildScope({ ...model, [field]: next }));
  };

  return (
    <div className="space-y-3 text-sm">
      <fieldset className="space-y-1" disabled={disabled}>
        <legend className="sr-only">{t('settings.extensions.drawer.scopeTitle')}</legend>
        {MODES.map((mode) => (
          <label key={mode} className="flex items-start gap-2">
            <input
              type="radio"
              name="plugin-scope-mode"
              className="mt-1"
              checked={model.mode === mode}
              onChange={() =>
                onChange(
                  buildScope({
                    mode,
                    channels: mode === model.mode ? model.channels : [],
                    contacts: mode === model.mode ? model.contacts : [],
                  })
                )
              }
            />
            <span>{t(`settings.extensions.scope.${mode}`)}</span>
          </label>
        ))}
      </fieldset>
      {listMode && (
        <div className="grid gap-3 sm:grid-cols-2">
          <ScopeList
            title={t('settings.extensions.scope.channels')}
            items={channels.map((c) => ({ key: c.key, label: c.name }))}
            selected={model.channels}
            onToggle={(key) => toggle('channels', key)}
            disabled={disabled}
          />
          <ScopeList
            title={t('settings.extensions.scope.contacts')}
            items={contacts.map((c) => ({
              key: c.public_key,
              label: c.name || c.public_key.slice(0, 12),
            }))}
            selected={model.contacts}
            onToggle={(key) => toggle('contacts', key)}
            disabled={disabled}
          />
        </div>
      )}
    </div>
  );
}

function ScopeList({
  title,
  items,
  selected,
  onToggle,
  disabled,
}: {
  title: string;
  items: { key: string; label: string }[];
  selected: string[];
  onToggle: (key: string) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <div className="mb-1 font-medium">{title}</div>
      <ul className="max-h-40 overflow-y-auto rounded-md border border-border p-2">
        {items.map((item) => (
          <li key={item.key}>
            <label className="flex items-center gap-2 py-0.5">
              <input
                type="checkbox"
                checked={selected.includes(item.key)}
                disabled={disabled}
                onChange={() => onToggle(item.key)}
              />
              <span className="min-w-0 break-words">{item.label}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
