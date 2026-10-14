import { useCallback, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import { CommunityLeaveDialog } from '../components/CommunityLeaveDialog';
import { toast } from '../components/ui/sonner';
import { setCommunityStatus } from '../stores/communityStore';
import type { CommunityOptOutReason } from '../types';

/**
 * Leave Community: a confirmation dialog (what Community keeps and deletes, an
 * optional reason), then `PATCH /api/community {enabled: false, opt_out_reason?}`,
 * the one call the browser makes to opt out. The backend sends Community's
 * `POST /v1/me/opt-out` first; when Community does not confirm it, the answer
 * carries `opt_out_pending` and the settings offer a retry. Shared by the
 * settings and the waiting-for-position banner: `requestLeave()` opens the
 * dialog, which the caller renders (`leaveDialog`); `onLeft` runs once
 * Community is off.
 */
export function useCommunityLeave(onLeft?: () => void): {
  requestLeave: () => void;
  leaving: boolean;
  leaveDialog: ReactNode;
} {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // A fresh dialog (no reason picked) each time it opens.
  const [generation, setGeneration] = useState(0);
  const [leaving, setLeaving] = useState(false);

  const requestLeave = useCallback(() => {
    setGeneration((value) => value + 1);
    setOpen(true);
  }, []);

  const confirm = useCallback(
    async (reason: CommunityOptOutReason | undefined) => {
      setLeaving(true);
      try {
        const status = await api.updateCommunity(
          reason ? { enabled: false, opt_out_reason: reason } : { enabled: false }
        );
        setCommunityStatus(status);
        if (status.opt_out_pending) {
          toast.error(t('settings.community.optOut.notConfirmedToast'));
        } else {
          toast.success(t('settings.community.left'));
        }
        setOpen(false);
        onLeft?.();
      } catch (err) {
        toast.error(formatApiError(err, t));
      } finally {
        setLeaving(false);
      }
    },
    [onLeft, t]
  );

  const leaveDialog = (
    <CommunityLeaveDialog
      key={generation}
      open={open}
      leaving={leaving}
      onOpenChange={setOpen}
      onConfirm={(reason) => void confirm(reason)}
    />
  );
  return { requestLeave, leaving, leaveDialog };
}
