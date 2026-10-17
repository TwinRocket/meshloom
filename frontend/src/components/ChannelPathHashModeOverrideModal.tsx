import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Label } from './ui/label';

function pathHashModeLabel(mode: number, t: (key: string) => string): string {
  if (mode === 0) return t('channelInfo.hop1');
  if (mode === 1) return t('channelInfo.hop2');
  if (mode === 2) return t('channelInfo.hop3');
  return String(mode);
}

interface ChannelPathHashModeOverrideModalProps {
  open: boolean;
  onClose: () => void;
  channelName: string;
  currentOverride: number | null;
  radioDefault: number;
  onSetOverride: (value: number | null) => void;
}

export function ChannelPathHashModeOverrideModal({
  open,
  onClose,
  channelName,
  currentOverride,
  radioDefault,
  onSetOverride,
}: ChannelPathHashModeOverrideModalProps) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<number | null>(null);

  // Reseed the choice whenever the dialog opens or the override changes while open.
  const [seededFrom, setSeededFrom] = useState<{
    open: boolean;
    currentOverride: number | null;
  } | null>(null);
  if (seededFrom?.open !== open || seededFrom.currentOverride !== currentOverride) {
    setSeededFrom({ open, currentOverride });
    if (open) setSelected(currentOverride);
  }

  const radioDefaultLabel = pathHashModeLabel(radioDefault, t);

  const options: { value: number | null; label: string; description: string }[] = [
    {
      value: null,
      label: t('channelInfo.radioDefault', { label: radioDefaultLabel }),
      description: t('channelInfo.radioDefaultHelp'),
    },
    {
      value: 0,
      label: t('channelInfo.hop1Label'),
      description: t('channelInfo.hop1Help'),
    },
    {
      value: 1,
      label: t('channelInfo.hop2Label'),
      description: t('channelInfo.hop2Help'),
    },
    {
      value: 2,
      label: t('channelInfo.hop3Label'),
      description: t('channelInfo.hop3Help'),
    },
  ];

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{t('channelInfo.pathHashTitle')}</DialogTitle>
          <DialogDescription>{t('channelInfo.pathHashDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
            <div className="font-medium">{channelName}</div>
            <div className="mt-1 text-muted-foreground">
              {t('channelInfo.currentOverride', {
                value:
                  currentOverride != null
                    ? pathHashModeLabel(currentOverride, t) ||
                      t('channelInfo.overrideMode', { mode: currentOverride })
                    : t('channelInfo.overrideNone', { label: radioDefaultLabel }),
              })}
            </div>
          </div>

          <div className="space-y-2">
            <Label>{t('channelInfo.hopWidthLabel')}</Label>
            <div className="space-y-1.5">
              {options.map((opt) => (
                <button
                  key={String(opt.value)}
                  type="button"
                  className={`w-full rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                    selected === opt.value
                      ? 'border-primary bg-primary/10 text-foreground'
                      : 'border-border hover:bg-accent'
                  }`}
                  onClick={() => setSelected(opt.value)}
                >
                  <div className="font-medium">{opt.label}</div>
                  <div className="text-xs text-muted-foreground">{opt.description}</div>
                </button>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2 sm:block sm:space-x-0">
          <Button
            type="button"
            className="w-full"
            onClick={() => {
              onSetOverride(selected);
              onClose();
            }}
          >
            {selected == null
              ? t('channelInfo.useRadioDefault', { name: channelName })
              : t('channelInfo.useHops', {
                  width: pathHashModeLabel(selected, t),
                  name: channelName,
                })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
