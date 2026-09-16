import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LogIn } from 'lucide-react';

import {
  edgeSessionLost,
  onEdgeSessionExpired,
  reauthenticate,
  reportEdgeSessionExpired,
} from '../utils/edgeSession';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';

/**
 * Says the session is over, and offers the only thing that can end it.
 *
 * Deliberately not an automatic reload. If the detection is ever wrong, an
 * automatic one reloads forever; a button that the reader presses cannot.
 */
export function EdgeSessionExpiredDialog() {
  const { t } = useTranslation();
  const [expired, setExpired] = useState(false);

  useEffect(() => onEdgeSessionExpired(() => setExpired(true)), []);

  useEffect(() => {
    // An installed app is usually reopened long after it was last used, which is
    // exactly when the session has gone. Asking on the way back means the first
    // thing the reader does is not the thing that fails.
    const check = () => {
      if (document.visibilityState !== 'visible') return;
      void edgeSessionLost().then((lost) => {
        if (lost) reportEdgeSessionExpired();
      });
    };
    document.addEventListener('visibilitychange', check);
    return () => document.removeEventListener('visibilitychange', check);
  }, []);

  if (!expired) return null;

  return (
    <Dialog open>
      <DialogContent
        data-testid="edge-session-expired-dialog"
        className="sm:max-w-md"
        hideCloseButton
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{t('edgeSession.title')}</DialogTitle>
          <DialogDescription>{t('edgeSession.body')}</DialogDescription>
        </DialogHeader>
        <Button type="button" onClick={reauthenticate} className="w-full">
          <LogIn className="mr-2 h-4 w-4" aria-hidden="true" />
          {t('edgeSession.action')}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
