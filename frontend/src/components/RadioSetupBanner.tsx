import { RadioTower } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { HealthStatus } from '../types';
import { Button } from './ui/button';

/**
 * Whether the instance has no radio to talk to yet.
 *
 * Only an explicit `false` counts. The field is optional, and a server that does
 * not send it is not saying the transport is missing — treating silence as an
 * answer would put a setup banner in front of a working installation.
 */
export function radioSetupBannerVisible(health: HealthStatus | null): boolean {
  return health?.transport_configured === false;
}

/**
 * The one thing to do when nothing else in the app can work yet.
 *
 * Without a transport there are no contacts, no messages and no packets, so every
 * view is empty for a reason the views themselves cannot explain. The status chip
 * reports the same state, but it is a dot in a corner — right for a radio that
 * dropped, too quiet for one that was never configured.
 */
export function RadioSetupBanner({
  health,
  onOpenRadioSettings,
}: {
  health: HealthStatus | null;
  onOpenRadioSettings: () => void;
}) {
  const { t } = useTranslation();
  if (!radioSetupBannerVisible(health)) return null;

  return (
    <div
      data-testid="radio-setup-banner"
      role="status"
      className="flex flex-col gap-3 border-b border-primary/30 bg-primary/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
    >
      <div className="flex items-start gap-3">
        <RadioTower className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
        <div className="space-y-1">
          <p className="text-sm font-semibold text-foreground">{t('radioSetup.bannerTitle')}</p>
          <p className="text-[0.8125rem] leading-snug text-muted-foreground">
            {t('radioSetup.bannerBody')}
          </p>
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        className="shrink-0 self-start sm:self-auto"
        onClick={onOpenRadioSettings}
      >
        {t('radioSetup.bannerAction')}
      </Button>
    </div>
  );
}
