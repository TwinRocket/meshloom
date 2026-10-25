import { useTranslation } from 'react-i18next';

import { Button } from '../../ui/button';

/**
 * Shown when the health payload carries the bot-migration warning (it arrives with the
 * switch, Store T9). The section mounts it only then; acknowledging clears the warning.
 */
export function MigrationBanner({
  onAcknowledge,
  busy,
}: {
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
      <p>{t('settings.extensions.migration.text')}</p>
      <Button size="sm" onClick={onAcknowledge} disabled={busy}>
        {t('settings.extensions.migration.acknowledge')}
      </Button>
    </div>
  );
}
