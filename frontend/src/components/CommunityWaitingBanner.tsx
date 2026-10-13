import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCommunityLeave } from '../hooks/useCommunityLeave';
import { useCommunityPositionPicker } from './CommunityPositionPicker';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

/**
 * Community is on, but the radio has no position it may publish
 * (`CommunityStatus.state === 'waiting_for_position'`): only its MQTT status
 * leaves. The banner offers to choose a position (saved on the radio, which
 * then re-binds with Community) or to leave Community.
 *
 * A named region, not a live region: it is mounted with its content and holds
 * buttons, so a `role="status"` would be read whole, or not at all.
 */
export function CommunityWaitingBanner() {
  const { t } = useTranslation();
  const { leave, leaving } = useCommunityLeave();
  const { openPicker, saving, picker } = useCommunityPositionPicker();
  const [explainOpen, setExplainOpen] = useState(false);

  const choosePosition = () => {
    setExplainOpen(false);
    openPicker();
  };

  const handleLeave = async () => {
    if (await leave()) setExplainOpen(false);
  };

  return (
    <>
      <section
        data-testid="community-waiting-banner"
        aria-label={t('settings.community.waiting.regionLabel')}
        className="flex flex-col gap-3 border-b border-warning/30 bg-warning/10 px-4 py-3 text-warning sm:flex-row sm:items-center sm:justify-between"
      >
        <p className="text-[0.8125rem] font-medium leading-snug">
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
      </section>

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

      {picker}
    </>
  );
}
