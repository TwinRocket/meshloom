import { useTranslation } from 'react-i18next';

import { Button } from '../components/ui/button';
import { AuthLayout } from './AuthLayout';

/**
 * Reached through Home Assistant ingress with no Home Assistant user (an anonymous
 * ingress session, a direct link, a disabled account): Home Assistant is the only way
 * in there, so this replaces the login and setup forms.
 */
export function IngressSignInPage({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <AuthLayout title={t('auth.ingress.title')} description={t('auth.ingress.body')}>
      <p className="text-center text-sm text-muted-foreground">{t('auth.ingress.disabled')}</p>
      <Button type="button" className="w-full" onClick={onRetry}>
        {t('auth.ingress.retry')}
      </Button>
    </AuthLayout>
  );
}
