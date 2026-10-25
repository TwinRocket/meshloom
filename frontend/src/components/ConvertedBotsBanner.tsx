import { Puzzle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { HealthStatus } from '../types';
import { Button } from './ui/button';

/**
 * Whether the converted-bots notice is still pending (`health.converted_bots`, cleared
 * by the acknowledgement in Settings > Extensions). Administrators only: the
 * extensions are theirs to review.
 */
export function convertedBotsBannerVisible(health: HealthStatus | null, isAdmin: boolean): boolean {
  return isAdmin && !!health?.converted_bots;
}

/**
 * The 5.0.0 switch turned the user's bots into extensions that stay off until an
 * administrator has accepted what they may do. A notice inside the extensions section
 * is only seen by someone who goes there, so this one follows every view until the
 * notice is acknowledged (the acknowledgement stays in Settings > Extensions, next to
 * the review it stands for).
 */
export function ConvertedBotsBanner({
  health,
  isAdmin,
  onOpenExtensions,
}: {
  health: HealthStatus | null;
  isAdmin: boolean;
  onOpenExtensions: () => void;
}) {
  const { t } = useTranslation();
  if (!convertedBotsBannerVisible(health, isAdmin)) return null;
  const { converted, attention } = health!.converted_bots!;

  return (
    <div
      data-testid="converted-bots-banner"
      role="status"
      className="flex flex-col gap-3 border-b border-warning/30 bg-warning/10 px-4 py-3 text-warning sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-3">
        <Puzzle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <p className="text-[0.8125rem] font-medium leading-snug">
          {t('settings.extensions.migration.bannerText', { count: converted, attention })}
        </p>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="shrink-0 self-start border-warning/50 text-warning hover:bg-warning/10 sm:self-auto"
        onClick={onOpenExtensions}
      >
        {t('settings.extensions.migration.bannerAction')}
      </Button>
    </div>
  );
}
