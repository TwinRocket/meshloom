import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LogOut } from 'lucide-react';

import { Button } from '../components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { useSession } from './SessionProvider';

/** The signed-in account: its display name, and the way out. Absent when nobody is known. */
export function UserMenu({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { user, role, via, signOut } = useSession();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!user) return null;
  const name = user.display_name || user.username;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t('auth.userMenu.open', { name })}
        title={name}
        className={className}
      >
        <span
          aria-hidden="true"
          className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold uppercase text-primary"
        >
          {[...name][0]}
        </span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="user-menu" className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{name}</DialogTitle>
            <DialogDescription>
              {user.username} · {t(`auth.role.${role}`)}
            </DialogDescription>
          </DialogHeader>
          {via === 'ingress' ? (
            // Home Assistant owns that identity: there is no Meshloom session to end.
            <p className="text-sm text-muted-foreground">{t('auth.userMenu.ingressNote')}</p>
          ) : (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void signOut();
              }}
            >
              <LogOut className="mr-2 h-4 w-4" aria-hidden="true" />
              {t('auth.userMenu.signOut')}
            </Button>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
