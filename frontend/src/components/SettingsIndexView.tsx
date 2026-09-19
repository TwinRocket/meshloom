import { useTranslation } from 'react-i18next';
import { ChevronRight, PanelLeftClose } from 'lucide-react';
import {
  SETTINGS_SECTION_ICONS,
  SETTINGS_SECTION_LABELS,
  type SettingsSection,
} from './settings/settingsConstants';
import { RadioStatusChip } from './RadioStatusChip';
import type { HealthStatus } from '../types';
import { cn } from '../lib/utils';

/**
 * The settings index, for phones.
 *
 * Settings are reached from the bar now, and the bar has no notion of which section
 * you want — it used to drop straight into the radio one, with the rail that would
 * have let you choose gone with the drawer. So there has to be a screen whose job is
 * the choosing.
 *
 * Grouped rather than one long list: ten sections in a single column is a wall, and
 * the grouping is the same one the reader already carries — the radio, this app, the
 * data it keeps.
 */

interface Props {
  onSelectSection: (section: SettingsSection) => void;
  disabledSections?: SettingsSection[];
  health?: HealthStatus | null;
  /** Opens the radio read-out the phone-header chip summarises. */
  onOpenRadioStatus?: () => void;
  /** The section open beside this list. Desktop only; a phone shows one at a time. */
  activeSection?: SettingsSection;
  updateAvailable?: boolean;
  /** Desktop: fold the index column so the section can use the width. */
  onCollapseList?: () => void;
}

const GROUPS: { titleKey: string; sections: SettingsSection[] }[] = [
  { titleKey: 'settingsIndex.groupRadio', sections: ['radio', 'proxy', 'radio-app', 'alerts'] },
  {
    titleKey: 'settingsIndex.groupApp',
    sections: ['local', 'navigation', 'notifications', 'updates', 'community', 'fanout'],
  },
  { titleKey: 'settingsIndex.groupData', sections: ['database', 'statistics', 'about'] },
];

export function SettingsIndexView({
  onSelectSection,
  disabledSections = [],
  health,
  onOpenRadioStatus,
  activeSection,
  updateAvailable = false,
  onCollapseList,
}: Props) {
  const { t } = useTranslation();

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 bg-background px-4 pb-2 pt-8 md:pt-5">
        {onCollapseList && (
          <button
            type="button"
            onClick={onCollapseList}
            aria-label={t('conversationList.collapseList')}
            className="-ml-1.5 hidden h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:inline-flex"
          >
            <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
        <h1 className="min-w-0 truncate text-2xl font-semibold tracking-tight">
          {t('settingsIndex.title')}
        </h1>
        <RadioStatusChip
          health={health ?? null}
          onOpenStatus={onOpenRadioStatus}
          updateAvailable={updateAvailable}
          className="ml-auto max-w-[9rem] md:hidden"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {GROUPS.map(({ titleKey, sections }) => (
          <section key={titleKey} className="mb-6">
            <h2 className="px-1 pb-2 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
              {t(titleKey)}
            </h2>
            {/* One card per group, rows inside it: the card is the boundary, so the
                rows only need a hairline between them, inset to the text. */}
            <ul className="overflow-hidden rounded-2xl bg-muted/40">
              {sections.map((section, index) => {
                const Icon = SETTINGS_SECTION_ICONS[section];
                const disabled = disabledSections.includes(section);
                const current = activeSection === section;
                return (
                  <li key={section}>
                    <button
                      type="button"
                      disabled={disabled}
                      aria-current={current ? 'page' : undefined}
                      onClick={() => onSelectSection(section)}
                      className={cn(
                        'flex w-full items-center gap-3 px-4 py-3.5 text-left',
                        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                        disabled
                          ? 'cursor-not-allowed opacity-50'
                          : current
                            ? 'bg-accent/60'
                            : 'active:bg-accent/40 md:hover:bg-accent/30'
                      )}
                    >
                      <Icon className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                      <span
                        className={cn(
                          'flex min-w-0 flex-1 items-center gap-2 pr-1',
                          index < sections.length - 1 && 'border-b border-border/25 pb-3.5 -mb-3.5'
                        )}
                      >
                        <span className="truncate">{t(SETTINGS_SECTION_LABELS[section])}</span>
                        {section === 'updates' && updateAvailable && (
                          <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider text-primary">
                            {t('updates.badgeLabel')}
                          </span>
                        )}
                        <ChevronRight
                          className="ml-auto h-4 w-4 shrink-0 text-muted-foreground"
                          aria-hidden="true"
                        />
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
