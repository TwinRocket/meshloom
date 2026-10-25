import { useTranslation } from 'react-i18next';

import { Button } from '../../ui/button';

/**
 * Shown while `health.converted_bots` is non-null: the legacy bots were converted into
 * extensions and an admin has not acknowledged it yet. The section mounts it only then;
 * acknowledging calls `POST /api/plugins/converted-bots/acknowledge`, which clears the notice.
 */
export function MigrationBanner({
  converted,
  attention,
  onAcknowledge,
  busy,
}: {
  converted?: number;
  attention?: number;
  onAcknowledge: () => void;
  busy?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      className="space-y-2 rounded-md border border-warning/50 bg-warning/10 p-3 text-sm"
    >
      <div className="font-medium">{t('settings.extensions.migration.title')}</div>
      {converted !== undefined && (
        <p data-testid="migration-counts">
          {t('settings.extensions.migration.counts', {
            count: converted,
            attention: attention ?? 0,
          })}
        </p>
      )}
      <p>{t('settings.extensions.migration.text')}</p>
      <Button size="sm" onClick={onAcknowledge} disabled={busy}>
        {t('settings.extensions.migration.acknowledge')}
      </Button>
    </div>
  );
}
