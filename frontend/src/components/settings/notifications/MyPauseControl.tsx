import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BellOff } from 'lucide-react';

import type { MyPushPause } from '../../../notifications/types';
import { Button } from '../../ui/button';
import { formatRemaining, pauseRemaining, pauseUntil, type PauseChoice } from './rulesShared';

const CHOICES: PauseChoice[] = ['1h', '8h', 'tomorrow'];

/**
 * "Do not disturb" for this account's own devices (MSHL-363): 1 h / 8 h / until tomorrow / until
 * I resume, then resume. Does not touch the administrator's pause or the other accounts.
 */
export function MyPauseControl({
  pause,
  onPause,
  onResume,
  disabled,
}: {
  pause: MyPushPause;
  /** Epoch seconds the pause ends at; `null` pauses until the account resumes. */
  onPause: (until: number | null) => void;
  onResume: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  const indefinite = pause.active && pause.until === null;
  const remaining = pauseRemaining(pause.until, now);
  // The server flags a pause active until its end; the clock here may be ahead of it by a poll.
  const active = indefinite || (pause.active && remaining > 0);
  const { hours, minutes } = formatRemaining(remaining);

  return (
    <div className="space-y-2" data-testid="my-notification-pause">
      <p role="status" className="text-sm">
        {active ? (
          <span className="inline-flex items-center gap-1.5 font-medium">
            <BellOff className="h-4 w-4" aria-hidden="true" />
            {indefinite
              ? t('settings.notifications.mine.pauseIndefinite')
              : t('settings.notifications.pause.active', { hours, minutes })}
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
            onClick={() => onPause(pauseUntil(choice, Date.now()))}
          >
            {t(`settings.notifications.pause.choice_${choice}`)}
          </Button>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onPause(null)}
        >
          {t('settings.notifications.mine.pauseUntilResume')}
        </Button>
        <Button
          type="button"
          variant={active ? 'default' : 'outline'}
          size="sm"
          disabled={disabled || !active}
          onClick={onResume}
        >
          {t('settings.notifications.pause.off')}
        </Button>
      </div>
    </div>
  );
}
