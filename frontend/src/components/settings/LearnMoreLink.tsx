import { useTranslation } from 'react-i18next';

import { userDocsUrl } from '../../utils/userDocsUrl';

/** "Learn more" link to a user docs page, shown after a short help line. */
export function LearnMoreLink({ page }: { page: string }) {
  const { t, i18n } = useTranslation();
  return (
    <a
      href={userDocsUrl(i18n.language, page)}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-2 hover:text-primary transition-colors"
    >
      {t('settings.common.learnMore')}
    </a>
  );
}
