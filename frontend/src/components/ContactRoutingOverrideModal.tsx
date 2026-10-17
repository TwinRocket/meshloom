import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../api';
import type { Contact } from '../types';
import {
  formatForcedRouteSummary,
  formatLearnedRouteSummary,
  formatRoutingOverrideInput,
} from '../utils/pathUtils';
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

interface ContactRoutingOverrideModalProps {
  open: boolean;
  onClose: () => void;
  contact: Contact;
  onSaved: (message: string) => void;
  onError: (message: string) => void;
}

export function ContactRoutingOverrideModal({
  open,
  onClose,
  contact,
  onSaved,
  onError,
}: ContactRoutingOverrideModalProps) {
  const { t } = useTranslation();
  const [route, setRoute] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reseed the input whenever the dialog opens or the contact changes while open.
  const [seededFrom, setSeededFrom] = useState<{ open: boolean; contact: Contact } | null>(null);
  if (seededFrom?.open !== open || seededFrom.contact !== contact) {
    setSeededFrom({ open, contact });
    if (open) {
      setRoute(formatRoutingOverrideInput(contact));
      setError(null);
    }
  }

  const learnedRouteSummary = useMemo(() => formatLearnedRouteSummary(contact), [contact]);
  const forcedRouteSummary = useMemo(() => formatForcedRouteSummary(contact), [contact]);

  const saveRoute = async (value: string) => {
    setSaving(true);
    setError(null);
    try {
      await api.setContactRoutingOverride(contact.public_key, value);
      onSaved(
        value.trim() === '' ? t('contactInfo.overrideCleared') : t('contactInfo.overrideUpdated')
      );
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : t('contactInfo.overrideFailed');
      setError(message);
      onError(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{t('contactInfo.routingTitle')}</DialogTitle>
          <DialogDescription>{t('contactInfo.routingDescription')}</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void saveRoute(route);
          }}
        >
          <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
            <div className="font-medium">{contact.name || contact.public_key.slice(0, 12)}</div>
            <div className="mt-1 text-muted-foreground">
              {t('contactInfo.learnedRouteCurrent', { summary: learnedRouteSummary })}
            </div>
            {forcedRouteSummary && (
              <div className="mt-1 text-destructive">
                {t('contactInfo.forcedRouteCurrent', { summary: forcedRouteSummary })}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="routing-override-input">{t('contactInfo.forcedRoute')}</Label>
            <Input
              id="routing-override-input"
              value={route}
              onChange={(event) => setRoute(event.target.value)}
              placeholder={t('contactInfo.forcedRoutePlaceholder')}
              autoFocus
              disabled={saving}
            />
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>{t('contactInfo.forcedRouteHelp')}</p>
              <p>{t('contactInfo.forcedRouteNote')}</p>
            </div>
          </div>

          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant="outline"
                className="w-full"
                onClick={() => void saveRoute('-1')}
                disabled={saving}
              >
                {t('contactInfo.forceFlood')}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="w-full"
                onClick={() => void saveRoute('0')}
                disabled={saving}
              >
                {t('contactInfo.forceDirect')}
              </Button>
            </div>
            <Button type="submit" className="w-full" disabled={saving || route.trim().length === 0}>
              {saving
                ? t('contactInfo.saving')
                : route.trim() === ''
                  ? t('contactInfo.forceCustomEmpty')
                  : t('contactInfo.forceCustom', { route: route.trim() })}
            </Button>
          </div>

          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          <DialogFooter className="gap-2 sm:justify-between">
            <Button type="button" variant="secondary" onClick={onClose} disabled={saving}>
              {t('contactInfo.cancel')}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => void saveRoute('')}
              disabled={saving}
            >
              {t('contactInfo.clearOverride')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
