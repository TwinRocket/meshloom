import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft } from 'lucide-react';
import { cn } from '../lib/utils';

/**
 * The title block of a tool pane — map, live, visualizer, packet feed, trace, locate.
 *
 * On a phone these are sub-screens of Tools, reached exactly as a settings section
 * is reached from the settings index, so they carry the same chrome: a round glass
 * control back to where the reader came from, and the pane's name centred over the
 * row rather than trailing the button, so it holds still as panes with longer names
 * come and go.
 *
 * Desktop keeps the dense rule-under-a-line title. There the pane sits beside a rail
 * that already says where you are, and nothing needs a way back.
 */

interface Props {
  title: ReactNode;
  /** Omitted on the panes that are destinations of the bar rather than sub-screens. */
  onBack?: () => void;
  /** Help text under the title. Muted and small on both size classes. */
  subtitle?: ReactNode;
  /** Controls belonging to the pane, kept at the end of the title row. */
  actions?: ReactNode;
  className?: string;
}

export function ToolPaneHeader({ title, onBack, subtitle, actions, className }: Props) {
  const { t } = useTranslation();

  return (
    <div
      className={cn(
        'shrink-0 bg-background px-4 pb-2 pt-8 md:border-b md:border-border md:py-2.5',
        className
      )}
    >
      <div className="flex items-center gap-2">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            aria-label={t('shell.backToTools')}
            className="liquid-surface glass-back-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronLeft className="h-[1.375rem] w-[1.375rem]" aria-hidden="true" />
          </button>
        )}
        <h2 className="min-w-0 flex-1 truncate text-center text-2xl font-semibold tracking-tight md:text-left md:text-base md:font-semibold md:tracking-normal">
          {title}
        </h2>
        {actions ? (
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        ) : (
          // Balances the back control so the title sits on the row's centre, not
          // on the centre of what is left of it.
          onBack && <span className="h-10 w-10 shrink-0 md:hidden" aria-hidden="true" />
        )}
      </div>
      {subtitle && <div className="mt-1 text-sm font-normal text-muted-foreground">{subtitle}</div>}
    </div>
  );
}
