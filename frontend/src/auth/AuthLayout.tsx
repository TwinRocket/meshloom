import type { ReactNode } from 'react';

import { useTranslation } from 'react-i18next';

import i18n from '../i18n';
import {
  APP_LANGUAGES,
  getSavedLanguage,
  setSavedLanguage,
  type AppLanguage,
} from '../utils/languagePreference';

/** The card both pages sit in: the mark, a title, and the language switch. */
export function AuthLayout({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <main className="flex h-dvh overflow-y-auto bg-background p-4" data-testid="auth-layout">
      <div className="m-auto w-full max-w-sm space-y-5 rounded-lg border border-border bg-card p-6 text-card-foreground shadow-sm">
        <div className="flex flex-col items-center gap-2 text-center">
          <img src="./meshloom-mark.svg" alt="" className="h-10 w-10 object-contain" />
          <h1 className="text-lg font-semibold">{title}</h1>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
        {children}
        <div className="flex justify-center gap-3 text-xs text-muted-foreground">
          {APP_LANGUAGES.map((code) => (
            <button
              key={code}
              type="button"
              lang={code}
              aria-pressed={getSavedLanguage() === code}
              onClick={() => switchLanguage(code)}
              className="underline-offset-2 hover:underline aria-pressed:font-semibold aria-pressed:text-foreground"
            >
              {t(`auth.language.${code}`)}
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}

function switchLanguage(code: AppLanguage): void {
  setSavedLanguage(code);
  void i18n.changeLanguage(code);
}
