import { useTranslation } from 'react-i18next';

import { Button } from '../components/ui/button';
import { AuthLayout } from './AuthLayout';
import { TotpEnrolment } from './TotpEnrolment';

/**
 * Shown instead of the app while `require_2fa_for_admins` confines this administrator to
 * the enrolment routes: the server refuses every other route until the factor is on.
 */
export function EnrolmentRequiredPage({
  onEnrolled,
  onSignOut,
}: {
  /** The factor is on: re-read the session. */
  onEnrolled: () => void;
  onSignOut?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <AuthLayout
      title={t('auth.enrolmentRequired.title')}
      description={t('auth.enrolmentRequired.description')}
    >
      <TotpEnrolment onFinished={onEnrolled} />
      {onSignOut && (
        <Button type="button" variant="ghost" className="w-full" onClick={onSignOut}>
          {t('auth.userMenu.signOut')}
        </Button>
      )}
    </AuthLayout>
  );
}
