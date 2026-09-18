import { TriangleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { RadioConfig } from '../types';
import { Button } from './ui/button';

export function pathHashOneByteBannerVisible(config: RadioConfig | null): boolean {
  return Boolean(config?.path_hash_mode_supported && config.path_hash_mode === 0);
}

export function PathHashOneByteBanner({
  config,
  onOpenRadioSettings,
}: {
  config: RadioConfig | null;
  onOpenRadioSettings: () => void;
}) {
  const { t } = useTranslation();
  if (!pathHashOneByteBannerVisible(config)) return null;

  return (
    <div
      data-testid="path-hash-one-byte-banner"
      role="status"
      className="flex flex-col gap-3 border-b border-warning/50 bg-warning/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
    >
      <div className="flex items-start gap-3">
        <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-hidden="true" />
        <div className="space-y-1">
          <p className="text-sm font-semibold text-foreground">{t('pathHashBanner.title')}</p>
          <p className="text-[0.8125rem] leading-snug text-muted-foreground">
            {t('pathHashBanner.body')}
          </p>
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="shrink-0 self-start border-warning/50 text-warning hover:bg-warning/10 sm:self-auto"
        onClick={onOpenRadioSettings}
      >
        {t('pathHashBanner.action')}
      </Button>
    </div>
  );
}
