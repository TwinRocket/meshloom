import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';
import { KNOWN_STATUSES, statusTone, type StatusTone } from './pluginsShared';

const TONES: Record<StatusTone, string> = {
  ok: 'bg-success/15 text-success',
  warn: 'bg-warning/15 text-warning',
  bad: 'bg-destructive/15 text-destructive',
  muted: 'bg-muted text-muted-foreground',
};

export function Badge({ tone = 'muted', children }: { tone?: StatusTone; children: string }) {
  return (
    <span
      className={cn(
        'inline-flex max-w-full items-center rounded-full px-2 py-0.5 text-xs font-medium',
        TONES[tone]
      )}
    >
      <span className="truncate">{children}</span>
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const known = (KNOWN_STATUSES as readonly string[]).includes(status);
  return (
    <Badge tone={statusTone(status)}>
      {known ? t(`settings.extensions.status.${status}`) : status}
    </Badge>
  );
}
