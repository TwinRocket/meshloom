import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  UNSCOPED_OVERRIDE_MARKER,
  isUnscopedMarker,
  stripRegionScopePrefix,
} from '../utils/regionScope';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';

interface ChannelFloodScopeOverrideModalProps {
  open: boolean;
  onClose: () => void;
  roomName: string;
  currentOverride: string | null;
  onSetOverride: (value: string) => void;
}

export function ChannelFloodScopeOverrideModal({
  open,
  onClose,
  roomName,
  currentOverride,
  onSetOverride,
}: ChannelFloodScopeOverrideModalProps) {
  const { t } = useTranslation();
  const [region, setRegion] = useState('');

  // Reseed the input whenever the dialog opens or the override changes while open.
  const [seededFrom, setSeededFrom] = useState<{
    open: boolean;
    currentOverride: string | null;
  } | null>(null);
  if (seededFrom?.open !== open || seededFrom.currentOverride !== currentOverride) {
    setSeededFrom({ open, currentOverride });
    // The unscoped marker isn't a region name, so start the input blank for it.
    if (open)
      setRegion(isUnscopedMarker(currentOverride) ? '' : stripRegionScopePrefix(currentOverride));
  }

  const trimmedRegion = region.trim();

  const currentOverrideLabel = isUnscopedMarker(currentOverride)
    ? t('channelInfo.unscopedLabel')
    : currentOverride
      ? stripRegionScopePrefix(currentOverride)
      : t('channelInfo.inheritLabel');

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{t('channelInfo.floodTitle')}</DialogTitle>
          <DialogDescription>{t('channelInfo.floodDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
            <div className="font-medium">{roomName}</div>
            <div className="mt-1 text-muted-foreground">
              {t('channelInfo.currentSetting', { value: currentOverrideLabel })}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel-region-input">{t('channelInfo.region')}</Label>
            <Input
              id="channel-region-input"
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              placeholder="Esperance"
              autoFocus
            />
          </div>
        </div>

        <DialogFooter className="gap-2 sm:block sm:space-x-0">
          <div className="space-y-2">
            <Button
              type="button"
              className="w-full"
              disabled={trimmedRegion.length === 0}
              onClick={() => {
                onSetOverride(trimmedRegion);
                onClose();
              }}
            >
              {trimmedRegion.length > 0
                ? t('channelInfo.scopeTo', { name: roomName, region: trimmedRegion })
                : t('channelInfo.scopeToRegion', { name: roomName })}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                onSetOverride(UNSCOPED_OVERRIDE_MARKER);
                onClose();
              }}
            >
              {t('channelInfo.alwaysUnscoped', { name: roomName })}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                onSetOverride('');
                onClose();
              }}
            >
              {t('channelInfo.useGlobal', { name: roomName })}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
