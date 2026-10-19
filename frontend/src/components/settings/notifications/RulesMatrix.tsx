import { Fragment, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lock } from 'lucide-react';

import type {
  NotificationChannel,
  NotificationEventInfo,
  NotificationRules,
  NotificationRulesPatch,
} from '../../../notifications/types';
import { Checkbox } from '../../ui/checkbox';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import {
  CONTACT_KINDS,
  eventKey,
  eventRule,
  groupEvents,
  kindChannels,
  toggledChannels,
  toggledContactTypes,
} from './rulesShared';

const MAX_GRACE_S = 3600;
const STICKY = 'sticky left-0 z-10 bg-background';

/**
 * Events (rows, grouped) by channels (columns). A cell is a checkbox; "always deliver" is a
 * column of its own. The table scrolls inside its box, the first column stays in view.
 */
export function RulesMatrix({
  rules,
  events,
  channels,
  disabled,
  onPatch,
}: {
  rules: NotificationRules;
  events: NotificationEventInfo[];
  channels: NotificationChannel[];
  disabled?: boolean;
  onPatch: (patch: NotificationRulesPatch) => void;
}) {
  const { t } = useTranslation();
  const groups = groupEvents(events);
  const [graceDraft, setGraceDraft] = useState(String(rules.radio_offline_grace_s));
  const [seenGrace, setSeenGrace] = useState(rules.radio_offline_grace_s);
  if (seenGrace !== rules.radio_offline_grace_s) {
    setSeenGrace(rules.radio_offline_grace_s);
    setGraceDraft(String(rules.radio_offline_grace_s));
  }

  const commitGrace = () => {
    const parsed = Number(graceDraft.trim());
    if (
      graceDraft.trim() === '' ||
      !Number.isInteger(parsed) ||
      parsed < 0 ||
      parsed > MAX_GRACE_S
    ) {
      setGraceDraft(String(rules.radio_offline_grace_s));
      return;
    }
    if (parsed !== rules.radio_offline_grace_s) onPatch({ radio_offline_grace_s: parsed });
  };

  const colSpan = channels.length + 2;
  const eventLabel = (id: string) =>
    t(`settings.notifications.matrix.events.${eventKey(id)}.label`);

  return (
    <div
      className="overflow-x-auto rounded-md border border-border/60"
      data-testid="notification-matrix"
    >
      <table className="w-full text-sm">
        <caption className="sr-only">{t('settings.notifications.matrix.caption')}</caption>
        <thead>
          <tr className="border-b border-border/60 text-left">
            <th scope="col" className={`${STICKY} min-w-36 px-3 py-2 font-medium`}>
              {t('settings.notifications.matrix.event')}
            </th>
            <th scope="col" className="px-2 py-2 text-center text-[0.75rem] font-medium">
              {t('settings.notifications.matrix.alwaysDeliver')}
            </th>
            {channels.map((channel) => (
              <th
                key={channel.id}
                scope="col"
                className={`min-w-20 px-2 py-2 text-center text-[0.75rem] font-medium ${
                  channel.enabled ? '' : 'text-muted-foreground opacity-60'
                }`}
              >
                <span className="block">{channel.name}</span>
                {!channel.enabled && (
                  <span className="block text-[0.625rem] font-normal">
                    {t('settings.notifications.matrix.channelOff')}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map(({ group, events: rows }) => (
            <Fragment key={group}>
              <tr className="bg-muted/40">
                <th
                  scope="rowgroup"
                  colSpan={colSpan}
                  className={`${STICKY} px-3 py-1.5 text-left text-[0.6875rem] font-semibold uppercase tracking-wider text-muted-foreground`}
                >
                  {t(`settings.notifications.matrix.groups.${group}`)}
                </th>
              </tr>
              {rows.map((event) => {
                const rule = eventRule(rules, event.id);
                const always = rule.always_deliver ?? event.urgent;
                const label = eventLabel(event.id);
                return (
                  <Fragment key={event.id}>
                    <tr className="border-b border-border/40">
                      <th scope="row" className={`${STICKY} px-3 py-2 text-left font-normal`}>
                        <span className="font-medium">{label}</span>
                        <span className="block text-[0.75rem] text-muted-foreground">
                          {t(`settings.notifications.matrix.events.${eventKey(event.id)}.help`)}
                        </span>
                      </th>
                      <td className="px-2 py-2 text-center">
                        <Checkbox
                          checked={always}
                          disabled={disabled}
                          aria-label={t('settings.notifications.matrix.alwaysDeliverFor', {
                            event: label,
                          })}
                          onCheckedChange={(v) =>
                            onPatch({ events: { [event.id]: { always_deliver: v === true } } })
                          }
                        />
                      </td>
                      {channels.map((channel) => {
                        const locked = event.locked_channels.includes(channel.id);
                        const checked = locked || rule.channels.includes(channel.id);
                        return (
                          <td key={channel.id} className="px-2 py-2 text-center">
                            <span className="inline-flex items-center gap-1">
                              <Checkbox
                                checked={checked}
                                disabled={disabled || locked}
                                aria-label={t('settings.notifications.matrix.cell', {
                                  event: label,
                                  channel: channel.name,
                                })}
                                onCheckedChange={(v) =>
                                  onPatch({
                                    events: {
                                      [event.id]: {
                                        channels: toggledChannels(
                                          rule.channels,
                                          channel.id,
                                          v === true
                                        ),
                                      },
                                    },
                                  })
                                }
                              />
                              {locked && (
                                <Lock
                                  className="h-3 w-3 text-muted-foreground"
                                  aria-label={t('settings.notifications.matrix.locked')}
                                />
                              )}
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                    {event.id === 'contact.first_seen' &&
                      CONTACT_KINDS.map((kind) => (
                        <tr key={kind} className="border-b border-border/40">
                          <th
                            scope="row"
                            className={`${STICKY} py-1.5 pl-7 pr-3 text-left text-[0.8125rem] font-normal text-muted-foreground`}
                          >
                            {t(`settings.notifications.matrix.contactTypes.${kind}`)}
                          </th>
                          <td />
                          {channels.map((channel) => (
                            <td key={channel.id} className="px-2 py-1.5 text-center">
                              <Checkbox
                                checked={kindChannels(rule, kind).includes(channel.id)}
                                disabled={disabled}
                                aria-label={t('settings.notifications.matrix.kindCell', {
                                  kind: t(`settings.notifications.matrix.contactTypes.${kind}`),
                                  channel: channel.name,
                                })}
                                onCheckedChange={(v) =>
                                  onPatch({
                                    events: {
                                      [event.id]: {
                                        contact_types: toggledContactTypes(
                                          rule,
                                          kind,
                                          channel.id,
                                          v === true
                                        ),
                                      },
                                    },
                                  })
                                }
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                    {event.id === 'radio.offline' && (
                      <tr className="border-b border-border/40">
                        <td colSpan={colSpan} className={`${STICKY} px-3 py-2`}>
                          <div className="flex flex-wrap items-center gap-2">
                            <Label htmlFor="matrix-radio-grace" className="text-[0.8125rem]">
                              {t('settings.notifications.radioOfflineGrace')}
                            </Label>
                            <Input
                              id="matrix-radio-grace"
                              type="number"
                              inputMode="numeric"
                              min={0}
                              max={MAX_GRACE_S}
                              step={1}
                              className="w-24"
                              value={graceDraft}
                              disabled={disabled}
                              onChange={(e) => setGraceDraft(e.target.value)}
                              onBlur={commitGrace}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') e.currentTarget.blur();
                              }}
                            />
                            <span className="text-[0.8125rem] text-muted-foreground">
                              {t('settings.notifications.radioOfflineGraceUnit')}
                            </span>
                          </div>
                        </td>
                      </tr>
                    )}
                    {event.id === 'message.mention' && (
                      <tr className="border-b border-border/40">
                        <td colSpan={colSpan} className={`${STICKY} px-3 py-2`}>
                          <div className="flex items-center gap-2">
                            <Checkbox
                              id="matrix-whole-word"
                              checked={rules.mention_whole_word}
                              disabled={disabled}
                              onCheckedChange={(v) => onPatch({ mention_whole_word: v === true })}
                            />
                            <Label htmlFor="matrix-whole-word" className="text-[0.8125rem]">
                              {t('settings.notifications.matrix.mentionWholeWord')}
                            </Label>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
