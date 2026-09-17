import { Trans, useTranslation } from 'react-i18next';
import type { HealthStatus, OssUpdateStatus } from '../../types';
import { Separator } from '../ui/separator';

const GITHUB_URL = 'https://github.com/TwinRocket/meshloom';

export function SettingsAboutSection({
  health,
  className,
  updates,
  onOpenUpdates,
}: {
  health?: HealthStatus | null;
  className?: string;
  updates?: OssUpdateStatus | null;
  onOpenUpdates?: () => void;
}) {
  const { t } = useTranslation();
  const version = health?.app_info?.version ?? 'unknown';
  const commit = health?.app_info?.commit_hash;
  const updateAvailable = updates?.update_available === true;

  return (
    <div className={className}>
      <div className="space-y-6">
        {/* Version */}
        <div className="text-center space-y-2">
          <div className="flex flex-col items-center gap-3">
            <img
              src="./meshloom-mark.svg"
              alt=""
              className="h-24 w-auto object-contain [filter:drop-shadow(0_0_16px_rgba(34,211,238,0.18))_drop-shadow(0_0_20px_rgba(191,90,242,0.2))]"
            />
            <h3
              aria-label="Meshloom"
              className="text-3xl font-semibold tracking-tight text-foreground"
            >
              <span>Mesh</span>
              <span className="bg-[linear-gradient(90deg,#22D3EE_0%,#BF5AF2_100%)] bg-clip-text text-transparent">
                loom
              </span>
            </h3>
          </div>
          <div className="text-sm text-muted-foreground">
            v{version}
            {commit ? (
              <>
                <span className="mx-1.5">·</span>
                <span className="font-mono text-xs" title={commit}>
                  {commit}
                </span>
              </>
            ) : null}
          </div>
          {updateAvailable ? (
            <button
              type="button"
              onClick={() => onOpenUpdates?.()}
              className="text-xs text-muted-foreground hover:text-primary hover:underline"
            >
              {t('settings.about.updateAvailable')}
            </button>
          ) : null}
        </div>

        <Separator />

        {/* Author & License */}
        <div className="text-sm text-center space-y-2">
          <p>
            <Trans
              i18nKey="settings.about.madeBy"
              components={{
                author: (
                  <a
                    href="https://github.com/bagl3y"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                  />
                ),
              }}
            />
          </p>
          <p>
            <Trans
              i18nKey="settings.about.licensed"
              components={{
                license: (
                  <a
                    href={`${GITHUB_URL}/blob/main/LICENSE.md`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                  />
                ),
              }}
            />
          </p>
        </div>

        <Separator />

        {/* Links */}
        <div className="flex justify-center gap-4 text-sm">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            {t('settings.about.github')}
          </a>
          <a
            href={`${GITHUB_URL}/issues`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            {t('settings.about.reportBug')}
          </a>
          <a
            href={`${GITHUB_URL}/blob/main/CHANGELOG.md`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            {t('settings.about.changelog')}
          </a>
        </div>

        <Separator />

        {/* Acknowledgements */}
        <div className="text-sm text-center text-muted-foreground space-y-2">
          <p>{t('settings.about.thanks')}</p>
          <p>
            <a
              href="https://github.com/meshcore-dev/MeshCore"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              MeshCore
            </a>
            <span className="mx-1.5">·</span>
            <a
              href="https://github.com/meshcore-dev/meshcore_py"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              meshcore_py
            </a>
          </p>
        </div>

        <Separator />

        <div className="text-center">
          <a
            href="./api/debug"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-primary hover:underline"
          >
            {t('settings.about.debugSnapshot')}
          </a>
        </div>
      </div>
    </div>
  );
}
