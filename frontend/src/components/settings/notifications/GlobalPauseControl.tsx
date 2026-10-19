import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BellOff } from 'lucide-react';

import { Button } from '../../ui/button';
import { formatRemaining, pauseRemaining, pauseUntil, type PauseChoice } from './rulesShared';

const CHOICES: PauseChoice[] = ['1h', '8h', 'tomorrow'];

/**
 * "Do not disturb": 1 h / 8 h / until tomorrow / off. Writes `rules.paused_until` through
 * `onChange` (epoch seconds, `null` resumes). Also used by the conversation bell.
 */
export function GlobalPauseControl({
  pausedUntil,
  onChange,
  disabled,
}: {
  pausedUntil: number | null;
  onChange: (until: number | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  const remaining = pauseRemaining(pausedUntil, now);
  const active = remaining > 0;
  const { hours, minutes } = formatRemaining(remaining);

  return (
    <div className="space-y-2" data-testid="notification-pause">
      <p role="status" className="text-sm">
        {active ? (
          <span className="inline-flex items-center gap-1.5 font-medium">
            <BellOff className="h-4 w-4" aria-hidden="true" />
            {t('settings.notifications.pause.active', { hours, minutes })}
          </span>
        ) : (
          <span className="text-muted-foreground">
            {t('settings.notifications.pause.inactive')}
          </span>
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        {CHOICES.map((choice) => (
          <Button
            key={choice}
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => onChange(pauseUntil(choice, Date.now()))}
          >
            {t(`settings.notifications.pause.choice_${choice}`)}
          </Button>
        ))}
        <Button
          type="button"
          variant={active ? 'default' : 'outline'}
          size="sm"
          disabled={disabled || !active}
          onClick={() => onChange(null)}
        >
          {t('settings.notifications.pause.off')}
        </Button>
      </div>
    </div>
  );
}
