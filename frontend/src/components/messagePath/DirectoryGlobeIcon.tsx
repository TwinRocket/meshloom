import { Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** Orange globe for a directory hop name when local resolve is not known. */
export function DirectoryGlobeIcon() {
  const { t } = useTranslation();
  return (
    <Globe
      aria-label={t('path.directoryGlobe')}
      data-testid="directory-globe-icon"
      className="size-3.5 shrink-0 text-orange-500"
    />
  );
}
