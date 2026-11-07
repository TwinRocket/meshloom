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
import { Label } from '../components/ui/label';
import { ThemeSelector } from '../components/settings/ThemeSelector';
import i18n from '../i18n';
import { APP_LANGUAGES, getSavedLanguage, setSavedLanguage } from '../utils/languagePreference';
import type { AppLanguage } from '../utils/languagePreference';
import { useSession } from './SessionProvider';

/** The signed-in account: its display name, and the way out. Absent when nobody is known. */
export function UserMenu({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { user, role, via, signOut } = useSession();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stillSignedIn, setStillSignedIn] = useState(false);
  const [language, setLanguage] = useState(getSavedLanguage);
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
            <DialogTitle>{role === 'kiosk' ? t('capabilities.kioskMenu.title') : name}</DialogTitle>
            <DialogDescription>
              {role === 'kiosk'
                ? t(`auth.role.${role}`)
                : `${user.username} · ${t(`auth.role.${role}`)}`}
            </DialogDescription>
          </DialogHeader>
          {role === 'kiosk' ? (
            // A display has no profile and no session of its own to manage: the language
            // and the theme of this screen are all it offers.
            <div className="space-y-4" data-testid="kiosk-menu">
              <p className="text-sm text-muted-foreground">
                {t('capabilities.kioskMenu.description')}
              </p>
              <div className="space-y-2">
                <Label htmlFor="kiosk-language">{t('language.label')}</Label>
                <select
                  id="kiosk-language"
                  value={language}
                  onChange={(event) => {
                    const next = event.target.value as AppLanguage;
                    setLanguage(next);
                    setSavedLanguage(next);
                    void i18n.changeLanguage(next);
                  }}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                >
                  {APP_LANGUAGES.map((code) => (
                    <option key={code} value={code}>
                      {t(`language.${code}`)}
                    </option>
                  ))}
                </select>
              </div>
              <ThemeSelector />
            </div>
          ) : via === 'ingress' ? (
            // Home Assistant owns that identity: there is no Meshloom session to end.
            <p className="text-sm text-muted-foreground">{t('auth.userMenu.ingressNote')}</p>
          ) : (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void signOut()
                  .then((ended) => setStillSignedIn(!ended))
                  .finally(() => setBusy(false));
              }}
            >
              <LogOut className="mr-2 h-4 w-4" aria-hidden="true" />
              {t('auth.userMenu.signOut')}
            </Button>
          )}
          {stillSignedIn && (
            // The server signs this device in again (login-free on the local network).
            <p role="status" className="text-sm text-muted-foreground">
              {t('auth.userMenu.stillSignedInNote')}
            </p>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
