import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../../api';
import type { Contact, ContactGroup } from '../../types';
import { getContactDisplayName } from '../../utils/pubkey';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';

export function ContactGroupsEditor({ contacts }: { contacts: Contact[] }) {
  const { t } = useTranslation();
  const [groups, setGroups] = useState<ContactGroup[]>([]);
  const [newName, setNewName] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [addKey, setAddKey] = useState('');

  useEffect(() => {
    api
      .getContactGroups()
      .then(setGroups)
      .catch(() => {
        toast.error(t('settings.groupsLoadFailed'));
      });
  }, [t]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const created = await api.createContactGroup(name);
      setNewName('');
      setGroups((prev) => [...prev, created]);
      setExpandedId(created.id);
    } catch (err) {
      toast.error(t('settings.groupCreateFailed'), {
        description: err instanceof Error ? err.message : t('settings.radioApp.unknownError'),
      });
    }
  };

  const handleDelete = async (group: ContactGroup) => {
    try {
      await api.deleteContactGroup(group.id);
      setGroups((prev) => prev.filter((g) => g.id !== group.id));
      if (expandedId === group.id) setExpandedId(null);
    } catch (err) {
      toast.error(t('settings.groupDeleteFailed'), {
        description: err instanceof Error ? err.message : t('settings.radioApp.unknownError'),
      });
    }
  };

  const handleSetMembers = async (group: ContactGroup, publicKeys: string[]) => {
    try {
      const updated = await api.setContactGroupMembers(group.id, publicKeys);
      setGroups((prev) => prev.map((g) => (g.id === updated.id ? updated : g)));
    } catch (err) {
      toast.error(t('settings.groupUpdateFailed'), {
        description: err instanceof Error ? err.message : t('settings.radioApp.unknownError'),
      });
    }
  };

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-semibold">{t('settings.groupsTitle')}</h4>
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.groupsHelp')}</p>
      <div className="flex gap-2">
        <Input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t('settings.groupName')}
          maxLength={64}
          className="flex-1"
        />
        <Button variant="outline" onClick={() => void handleCreate()} disabled={!newName.trim()}>
          {t('settings.add')}
        </Button>
      </div>
      {groups.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">{t('settings.noGroups')}</p>
      ) : (
        <div className="space-y-2">
          {groups.map((group) => {
            const open = expandedId === group.id;
            const memberSet = new Set(group.public_keys);
            const candidates = contacts.filter((c) => !memberSet.has(c.public_key));
            return (
              <div key={group.id} className="rounded-md border border-border p-2 space-y-2">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="flex-1 text-left text-sm font-medium cursor-pointer hover:text-primary transition-colors"
                    onClick={() => setExpandedId(open ? null : group.id)}
                  >
                    {group.name}{' '}
                    <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                      {group.public_keys.length}
                    </span>
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => void handleDelete(group)}
                  >
                    {t('settings.delete')}
                  </Button>
                </div>
                {open && (
                  <div className="space-y-2">
                    {group.public_keys.length === 0 ? (
                      <p className="text-xs text-muted-foreground italic">
                        {t('settings.noMembers')}
                      </p>
                    ) : (
                      <div className="space-y-1">
                        {group.public_keys.map((key) => {
                          const contact = contacts.find((c) => c.public_key === key);
                          const label = contact
                            ? getContactDisplayName(
                                contact.name,
                                contact.public_key,
                                contact.last_advert
                              )
                            : key.slice(0, 12);
                          return (
                            <div key={key} className="flex items-center justify-between gap-2">
                              <span className="text-sm truncate">{label}</span>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 text-xs shrink-0"
                                onClick={() =>
                                  void handleSetMembers(
                                    group,
                                    group.public_keys.filter((k) => k !== key)
                                  )
                                }
                              >
                                {t('settings.remove')}
                              </Button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {candidates.length > 0 && (
                      <div className="flex gap-2">
                        <select
                          className="flex-1 h-9 rounded-md border border-input bg-background px-2 text-sm"
                          value={addKey}
                          onChange={(e) => setAddKey(e.target.value)}
                        >
                          <option value="">{t('settings.addContact')}</option>
                          {candidates.map((c) => (
                            <option key={c.public_key} value={c.public_key}>
                              {getContactDisplayName(c.name, c.public_key, c.last_advert)}
                            </option>
                          ))}
                        </select>
                        <Button
                          variant="outline"
                          disabled={!addKey}
                          onClick={() => {
                            if (!addKey) return;
                            void handleSetMembers(group, [...group.public_keys, addKey]);
                            setAddKey('');
                          }}
                        >
                          {t('settings.add')}
                        </Button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
