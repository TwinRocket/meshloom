import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import { toast } from '../components/ui/sonner';
import { setCommunityStatus } from '../stores/communityStore';

/**
 * Leave Community: `PATCH /api/community {enabled: false}`, the one call the
 * browser makes to opt out. Shared by the settings and the waiting-for-position
 * banner. Resolves `true` once Community is off.
 */
export function useCommunityLeave(): { leave: () => Promise<boolean>; leaving: boolean } {
  const { t } = useTranslation();
  const [leaving, setLeaving] = useState(false);
  const leave = useCallback(async () => {
    setLeaving(true);
    try {
      setCommunityStatus(await api.updateCommunity({ enabled: false }));
      toast.success(t('settings.community.left'));
      return true;
    } catch (err) {
      toast.error(formatApiError(err, t));
      return false;
    } finally {
      setLeaving(false);
    }
  }, [t]);
  return { leave, leaving };
}
