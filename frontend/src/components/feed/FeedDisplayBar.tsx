import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Minimize2 } from 'lucide-react';

import { Button } from '../ui/button';

const CLOCK_TICK_MS = 15_000;

interface FeedDisplayBarProps {
  /** `null` hides the direct-messages toggle (the session cannot read them). */
  showDms: boolean | null;
  showMedia: boolean;
  onShowDmsChange: (next: boolean) => void;
  onShowMediaChange: (next: boolean) => void;
  onExit: () => void;
}

/**
 * The only chrome of display mode: a discreet clock, the two opt-ins (direct messages, remote
 * media; both off when the mode opens) and Exit. Everything else of the feed's header is hidden.
 */
export function FeedDisplayBar({
  showDms,
  showMedia,
  onShowDmsChange,
  onShowMediaChange,
  onExit,
}: FeedDisplayBarProps) {
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
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 text-xs text-muted-foreground"
    >
      <time
        dateTime={new Date(now).toISOString()}
        aria-label={t('feed.display.clock')}
        className="tabular-nums"
      >
        {clock.format(now)}
      </time>
      <span className="flex-1" />
      {showDms !== null && (
        <label
          className="flex items-center gap-1.5 whitespace-nowrap"
          title={t('feed.display.showDmsNote')}
        >
          <input
            type="checkbox"
            checked={showDms}
            onChange={(event) => onShowDmsChange(event.target.checked)}
          />
          {t('feed.display.showDms')}
        </label>
      )}
      <label className="flex items-center gap-1.5 whitespace-nowrap">
        <input
          type="checkbox"
          checked={showMedia}
          onChange={(event) => onShowMediaChange(event.target.checked)}
        />
        {t('feed.display.showMedia')}
      </label>
      <Button type="button" size="sm" variant="ghost" onClick={onExit}>
        <Minimize2 className="h-4 w-4" aria-hidden="true" />
        {t('feed.display.exit')}
      </Button>
    </div>
  );
}
