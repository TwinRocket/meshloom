import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { OssUpdateStatus } from '../types';
import {
  readDismissedHold,
  updateHoldNeedsAttention,
  writeDismissedHold,
} from '../utils/updateHold';
import { Button } from './ui/button';

/**
 * Auto-update is on, but Community asked not to install this version on its own.
 * Dismissal is remembered per target version: the next held version shows it again.
 */
export function UpdateHoldBanner({
  updates,
  onOpenUpdates,
}: {
  updates: OssUpdateStatus | null | undefined;
  onOpenUpdates: () => void;
}) {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(readDismissedHold);
  const latest = updates?.latest;

  if (!updateHoldNeedsAttention(updates) || !latest || dismissed === latest) return null;

  const dismiss = () => {
    writeDismissedHold(latest);
    setDismissed(latest);
  };

  return (
    <section
      data-testid="update-hold-banner"
      aria-label={t('updates.holdRegionLabel')}
      className="flex flex-col gap-3 border-b border-primary/30 bg-primary/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <p className="text-[0.8125rem] font-medium leading-snug">
        {t('updates.holdBanner', { latest })}
      </p>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onOpenUpdates}>
          {t('updates.holdOpenSettings')}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={dismiss}>
          {t('updates.holdDismiss')}
        </Button>
      </div>
    </section>
  );
}
