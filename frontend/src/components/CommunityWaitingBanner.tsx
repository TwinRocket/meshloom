import { lazy, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import { useCommunityLeave } from '../hooks/useCommunityLeave';
import { refreshCommunityStatus } from '../stores/communityStore';
import { LazyBoundary } from './LazyBoundary';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { toast } from './ui/sonner';

// Leaflet stays out of the entry chunk: the picker loads only when opened.
const LocationPickerModal = lazy(() =>
  import('./LocationPickerModal').then((m) => ({ default: m.LocationPickerModal }))
);

/**
 * Community is on, but the radio has no position it may publish
 * (`CommunityStatus.state === 'waiting_for_position'`): only its MQTT status
 * leaves. The banner offers to choose a position (saved on the radio, which
 * then re-binds with Community) or to leave Community.
 */
export function CommunityWaitingBanner() {
  const { t } = useTranslation();
  const { leave, leaving } = useCommunityLeave();
  const [explainOpen, setExplainOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const messageId = useId();

  const choosePosition = () => {
    setExplainOpen(false);
    setPickerOpen(true);
  };

  // The radio stores the position (set_coords + appstart); its fresh self_info
  // ends the waiting state on the backend, which tells every tab.
  const savePosition = async (lat: number, lon: number) => {
    setSaving(true);
    try {
      await api.updateRadioConfig({ lat, lon });
      toast.success(t('settings.community.waiting.positionSaved'));
      void refreshCommunityStatus();
    } catch (err) {
      toast.error(t('settings.community.waiting.positionFailed'), {
        description: formatApiError(err, t),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleLeave = async () => {
    if (await leave()) setExplainOpen(false);
  };

  return (
    <>
      <div
        data-testid="community-waiting-banner"
        className="flex flex-col gap-3 border-b border-warning/30 bg-warning/10 px-4 py-3 text-warning sm:flex-row sm:items-center sm:justify-between"
        role="status"
      >
        <p id={messageId} className="text-[0.8125rem] font-medium leading-snug">
          {t('settings.community.waiting.banner')}
        </p>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="border-warning/50 text-warning hover:bg-warning/10"
            onClick={choosePosition}
            disabled={saving || leaving}
            aria-describedby={messageId}
          >
            {t('settings.community.waiting.chooseLocation')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-warning hover:bg-warning/10"
            onClick={() => setExplainOpen(true)}
            aria-haspopup="dialog"
          >
            {t('settings.community.waiting.learnMore')}
          </Button>
        </div>
      </div>

      <Dialog open={explainOpen} onOpenChange={setExplainOpen}>
        <DialogContent data-testid="community-waiting-dialog">
          <DialogHeader>
            <DialogTitle>{t('settings.community.waiting.title')}</DialogTitle>
            <DialogDescription>{t('settings.community.waiting.why')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[0.8125rem] text-muted-foreground">
            <p>{t('settings.community.waiting.privacy')}</p>
            <p>{t('settings.community.waiting.leaveHelp')}</p>
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => void handleLeave()}
              disabled={leaving || saving}
            >
              {leaving ? t('settings.community.leaving') : t('settings.community.waiting.leave')}
            </Button>
            <Button type="button" onClick={choosePosition} disabled={leaving || saving}>
              {t('settings.community.waiting.chooseLocation')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {pickerOpen && (
        <LazyBoundary fallback={null}>
          <LocationPickerModal
            open={pickerOpen}
            onOpenChange={setPickerOpen}
            initialLat=""
            initialLon=""
            onApply={(lat, lon) => void savePosition(lat, lon)}
          />
        </LazyBoundary>
      )}
    </>
  );
}
