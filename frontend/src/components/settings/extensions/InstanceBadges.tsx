import { useTranslation } from 'react-i18next';

import { Badge, StatusBadge } from './StatusBadge';
import { attentionTextKey, droppedFields } from './pluginsShared';
import type { PluginInstance } from './pluginsTypes';

/** Status, typed attention reason, settings an update reset, kind (tier) and the revoked/incompatible flags. */
export function InstanceBadges({ instance }: { instance: PluginInstance }) {
  const { t } = useTranslation();
  const reason = attentionTextKey(instance);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <StatusBadge status={instance.status} />
      {instance.status === 'needs_attention' && reason && (
        <Badge tone="warn">
          {t(`settings.extensions.attention.${reason}.badge`, {
            defaultValue: t('settings.extensions.attention.other.badge'),
          })}
        </Badge>
      )}
      {droppedFields(instance).length > 0 && (
        <Badge tone="warn">{t('settings.extensions.attention.config_dropped.badge')}</Badge>
      )}
      <Badge>{t(`settings.extensions.kind.${instance.kind}`)}</Badge>
    </div>
  );
}
