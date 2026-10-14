import { useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';

import type { CommunityOptOutReason } from '../types';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

export const COMMUNITY_OPT_OUT_REASONS: CommunityOptOutReason[] = [
  'privacy',
  'not_useful',
  'technical_issue',
  'other',
];

const PRIVACY_EMAIL = 'privacy@meshloom.app';

/**
 * Confirms an opt-out: what Community keeps and deletes, where to ask for a
 * full deletion, and an optional reason sent with `POST /v1/me/opt-out`.
 */
export function CommunityLeaveDialog({
  open,
  leaving,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  leaving: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (reason: CommunityOptOutReason | undefined) => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState<CommunityOptOutReason | 'none'>('none');

  return (
    <Dialog open={open} onOpenChange={(next) => !leaving && onOpenChange(next)}>
      <DialogContent data-testid="community-leave-dialog">
        <DialogHeader>
          <DialogTitle>{t('settings.community.optOut.title')}</DialogTitle>
          <DialogDescription>{t('settings.community.optOut.intro')}</DialogDescription>
        </DialogHeader>
        <ul className="list-disc space-y-1 pl-5 text-[0.8125rem] text-muted-foreground">
          <li>{t('settings.community.optOut.kept')}</li>
          <li>{t('settings.community.optOut.position')}</li>
          <li>{t('settings.community.optOut.advert')}</li>
          <li>
            <Trans
              i18nKey="settings.community.optOut.fullDeletion"
              components={{
                email: (
                  <a className="underline" href={`mailto:${PRIVACY_EMAIL}`}>
                    {PRIVACY_EMAIL}
                  </a>
                ),
              }}
            />
          </li>
        </ul>
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-[0.8125rem] font-medium">
            {t('settings.community.optOut.reasonLabel')}
          </legend>
          {(['none', ...COMMUNITY_OPT_OUT_REASONS] as const).map((value) => (
            <label key={value} className="flex items-center gap-2 text-[0.8125rem]">
              <input
                type="radio"
                name="community-opt-out-reason"
                value={value}
                checked={reason === value}
                onChange={() => setReason(value)}
                disabled={leaving}
              />
              {t(`settings.community.optOut.reasons.${value}`)}
            </label>
          ))}
        </fieldset>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={leaving}
          >
            {t('settings.community.optOut.cancel')}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => onConfirm(reason === 'none' ? undefined : reason)}
            disabled={leaving}
          >
            {leaving ? t('settings.community.leaving') : t('settings.community.optOut.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
