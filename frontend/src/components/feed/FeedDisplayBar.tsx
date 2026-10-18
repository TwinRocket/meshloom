import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Minimize2 } from 'lucide-react';

import { Button } from '../ui/button';

const CLOCK_TICK_MS = 15_000;

/** The only chrome of display mode: a discreet clock and Exit, the one control left. */
export function FeedDisplayBar({ onExit }: { onExit: () => void }) {
  const { t, i18n } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);
  const clock = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, { hour: '2-digit', minute: '2-digit' }),
    [i18n.language]
  );

  return (
    <div
      data-feed-display-bar=""
      className="flex shrink-0 items-center gap-3 px-4 py-1.5 text-sm text-muted-foreground"
    >
      <time
        dateTime={new Date(now).toISOString()}
        aria-label={t('feed.display.clock')}
        className="tabular-nums"
      >
        {clock.format(now)}
      </time>
      <span className="flex-1" />
      <Button type="button" size="sm" variant="ghost" onClick={onExit}>
        <Minimize2 className="h-4 w-4" aria-hidden="true" />
        {t('feed.display.exit')}
      </Button>
    </div>
  );
}
