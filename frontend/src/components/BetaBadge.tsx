import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';

/**
 * Small "Beta" pill shown next to the title of a feature that is still in beta
 * (Meshloom Remote, Links & Files). Titles only: never inside a sentence or a toast.
 */
export function BetaBadge({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <span
      data-testid="beta-badge"
      title={t('beta.title')}
      className={cn(
        'inline-flex shrink-0 items-center rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider text-primary',
        className
      )}
    >
      {t('beta.label')}
    </span>
  );
}
