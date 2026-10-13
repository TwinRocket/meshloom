import { lazy, useCallback, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import { refreshCommunityStatus } from '../stores/communityStore';
import { LazyBoundary } from './LazyBoundary';
import { Button } from './ui/button';
import { toast } from './ui/sonner';

// Leaflet stays out of the entry chunk: the picker loads only when opened.
const LocationPickerModal = lazy(() =>
  import('./LocationPickerModal').then((m) => ({ default: m.LocationPickerModal }))
);

/**
 * "Choose my position" while Community waits for one: the existing location
 * picker, then `PATCH /api/radio/config {lat, lon}`. The radio stores it
 * (set_coords + appstart); Community leaves the waiting state once the bind
 * carrying that position succeeds, and the backend tells every tab.
 */
export function useCommunityPositionPicker(): {
  openPicker: () => void;
  saving: boolean;
  picker: ReactNode;
} {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const save = useCallback(
    async (lat: number, lon: number) => {
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
    },
    [t]
  );

  const picker = open ? (
    <LazyBoundary fallback={null}>
      <LocationPickerModal
        open={open}
        onOpenChange={setOpen}
        initialLat=""
        initialLon=""
        onApply={(lat, lon) => void save(lat, lon)}
      />
    </LazyBoundary>
  ) : null;

  return { openPicker: useCallback(() => setOpen(true), []), saving, picker };
}

/**
 * What a Community surface shows instead of "Community is off" while
 * Community waits for the radio's position: why, and the way out.
 */
export function CommunityWaitingNotice({ testId }: { testId?: string }) {
  const { t } = useTranslation();
  const { openPicker, saving, picker } = useCommunityPositionPicker();
  return (
    <div className="space-y-2" data-testid={testId}>
      <p className="text-sm font-medium">{t('settings.community.waiting.title')}</p>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.community.waiting.featureOff')}
      </p>
      <Button type="button" variant="outline" size="sm" onClick={openPicker} disabled={saving}>
        {t('settings.community.waiting.chooseLocation')}
      </Button>
      {picker}
    </div>
  );
}
