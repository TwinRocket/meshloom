import { useEffect, useState } from 'react';
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
import { cn } from '../lib/utils';
import { preselectedFloodScope } from '../utils/meshTest';

/**
 * Which region the test flood is scoped to.
 *
 * The list is the instance's known regions, not free text: a scope the radio has
 * never heard of produces a flood nobody is listening for, and finding that out
 * from an empty result ten minutes later is the wrong way round. The server checks
 * the region again regardless — this list is a browser's copy of it.
 */

interface MeshTestRunDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  knownRegions: string[];
  /** The node's global flood scope, preselected when it is one of the known regions. */
  floodScope?: string;
  sending: boolean;
  onRun: (floodScope: string) => void;
  onOpenRadioSettings?: () => void;
}

export function MeshTestRunDialog({
  open,
  onOpenChange,
  knownRegions,
  floodScope,
  sending,
  onRun,
  onOpenRadioSettings,
}: MeshTestRunDialogProps) {
  const { t } = useTranslation();
  const [region, setRegion] = useState('');

  useEffect(() => {
    if (!open) return;
    setRegion(preselectedFloodScope(floodScope, knownRegions));
  }, [open, floodScope, knownRegions]);

  const empty = knownRegions.length === 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{t('meshTest.dialogTitle')}</DialogTitle>
          <DialogDescription>{t('meshTest.dialogDescription')}</DialogDescription>
        </DialogHeader>

        {empty ? (
          <p className="text-sm text-muted-foreground">{t('meshTest.noRegions')}</p>
        ) : (
          <div className="space-y-2">
            <Label>{t('meshTest.region')}</Label>
            <div
              role="radiogroup"
              aria-label={t('meshTest.region')}
              className="max-h-64 space-y-1 overflow-y-auto"
            >
              {knownRegions.map((candidate) => (
                <button
                  key={candidate}
                  type="button"
                  role="radio"
                  aria-checked={region === candidate}
                  onClick={() => setRegion(candidate)}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    region === candidate
                      ? 'border-primary bg-primary/10 font-medium'
                      : 'border-border hover:bg-accent/40'
                  )}
                >
                  <span
                    className={cn(
                      'h-2.5 w-2.5 shrink-0 rounded-full',
                      region === candidate ? 'bg-primary' : 'bg-muted-foreground/40'
                    )}
                    aria-hidden="true"
                  />
                  <span className="truncate">{candidate}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <DialogFooter className="gap-2">
          {empty ? (
            <Button
              type="button"
              onClick={() => {
                onOpenChange(false);
                onOpenRadioSettings?.();
              }}
            >
              {t('meshTest.openRadioSettings')}
            </Button>
          ) : (
            <Button type="button" disabled={sending || region === ''} onClick={() => onRun(region)}>
              {sending ? t('meshTest.sending') : t('meshTest.confirmRun')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
