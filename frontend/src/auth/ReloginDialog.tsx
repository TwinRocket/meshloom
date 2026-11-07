import { useTranslation } from 'react-i18next';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { AUTH_RESTORED_EVENT } from './authEvents';
import { LoginForm } from './LoginPage';
import type { AuthSessionInfo } from './types';

/**
 * The session ended while the app was open. The app stays mounted behind this dialog,
 * so the conversation, the draft and the scroll position are still there afterwards.
 * Signing in as someone else reloads instead: that state belonged to the other account.
 */
export function ReloginDialog({
  username,
  previousUserId,
  onDone,
}: {
  username: string;
  previousUserId: number | null;
  onDone: (session: AuthSessionInfo) => void;
}) {
  const { t } = useTranslation();

  return (
    <Dialog open>
      <DialogContent
        data-testid="relogin-dialog"
        className="sm:max-w-md"
        hideCloseButton
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{t('auth.relogin.title')}</DialogTitle>
          <DialogDescription>{t('auth.relogin.body')}</DialogDescription>
        </DialogHeader>
        <LoginForm
          initialUsername={username}
          autoFocusPassword
          onDone={(session) => {
            if (previousUserId !== null && session.user && session.user.id !== previousUserId) {
              window.location.reload();
              return;
            }
            onDone(session);
            window.dispatchEvent(new Event(AUTH_RESTORED_EVENT));
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
