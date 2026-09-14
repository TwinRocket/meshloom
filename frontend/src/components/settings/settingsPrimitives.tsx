import { useTranslation } from 'react-i18next';
import { ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * Shared chrome for settings groups.
 *
 * Settings in this app land in three different places — the radio's own flash,
 * Meshloom's database, and this browser — and used to look identical whichever it
 * was. Two adjacent checkboxes could have opposite persistence with nothing on
 * screen to tell them apart. These pieces exist so every group says where its
 * values go and whether anything is waiting to be saved.
 */

/** Where a group's values are written. Rendered next to the group title. */
export type StorageTarget = 'radio' | 'server' | 'browser';

const TARGET_KEYS: Record<StorageTarget, string> = {
  radio: 'settings.common.storedOnRadio',
  server: 'settings.common.storedInMeshloom',
  browser: 'settings.common.storedInBrowser',
};

export function SettingsGroupHeader({
  id,
  title,
  storedOn,
  instant,
  dirty,
}: {
  id?: string;
  title: string;
  storedOn: StorageTarget;
  /** Set when the group's controls take effect as soon as they change. */
  instant?: boolean;
  /** Set when the group holds edits an explicit Save has not written yet. */
  dirty?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h3 id={id} className="text-base font-semibold tracking-tight">
        {title}
      </h3>
      <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
        {t(TARGET_KEYS[storedOn])}
        {instant && ` · ${t('settings.common.appliesImmediately')}`}
      </span>
      {dirty && (
        <span className="text-[0.6875rem] text-warning" role="status">
          {t('settings.common.unsavedChanges')}
        </span>
      )}
    </div>
  );
}

/**
 * A bounded group. A hairline rule did not read as a boundary next to input borders.
 *
 * On phones the boundary is the filled card itself, the way the platform's own
 * settings draw it — a border there would land as a second rectangle inside the
 * rounded one. Desktop keeps the outlined card, which is denser.
 */
export function SettingsGroup({
  id,
  children,
  className,
}: {
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      aria-labelledby={id}
      className={`space-y-4 rounded-2xl bg-muted/40 p-4 md:rounded-lg md:border md:border-border md:bg-card/40 lg:p-5 ${className ?? ''}`}
    >
      {children}
    </section>
  );
}

/**
 * A block that is real work but rarely the reason someone opened the page.
 * Collapsed by default; groups that carry a save model are never hidden.
 */
export function AdvancedBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="group rounded-2xl bg-muted/40 md:rounded-lg md:border md:border-border md:bg-card/40">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-2xl px-4 py-3 md:rounded-lg text-base font-semibold tracking-tight hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:px-5">
        <ChevronRight
          className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90"
          aria-hidden="true"
        />
        {title}
      </summary>
      <div className="space-y-4 border-t border-border/40 px-4 py-4 md:border-border lg:px-5">
        {children}
      </div>
    </details>
  );
}

/**
 * A control and the paragraph that explains it, side by side once there is room.
 *
 * Stacked on narrow screens, which is the only thing that fits there. From lg the
 * explanation moves into a second column: it reads at a sane measure instead of
 * stretching the full pane, and the block stops being twice as tall as the control
 * it describes. Used on the blocks whose help text is long — elsewhere the pairing
 * buys nothing.
 */
export function SettingsField({
  help,
  children,
  className,
}: {
  help: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`gap-x-8 gap-y-2 lg:grid lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] lg:items-start ${className ?? ''}`}
    >
      <div className="space-y-2">{children}</div>
      <p className="mt-2 text-[0.8125rem] text-muted-foreground lg:mt-0">{help}</p>
    </div>
  );
}

/** Marks a tab whose group holds edits that have not been written yet. */
export function DirtyDot() {
  const { t } = useTranslation();
  return (
    <span
      className="ml-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-warning"
      role="img"
      aria-label={t('settings.common.unsavedChanges')}
    />
  );
}
