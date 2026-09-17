import { Trans, useTranslation } from 'react-i18next';
import type { HealthStatus, OssUpdateStatus } from '../../types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';

const GITHUB_URL = 'https://github.com/TwinRocket/meshloom';

export function SettingsAboutSection({
  health,
  className,
  updates,
  onOpenUpdate,
  onApply,
  onAutoUpdate,
}: {
  health?: HealthStatus | null;
  className?: string;
  updates?: OssUpdateStatus | null;
  onOpenUpdate?: () => void;
  onApply?: () => void;
  onAutoUpdate?: (enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  const version = health?.app_info?.version ?? 'unknown';
  const commit = health?.app_info?.commit_hash;
  const updateAvailable = updates?.update_available === true;
  const applySupported = updates?.apply_supported === true;

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
          {updateAvailable && (
            <div className="mx-auto max-w-sm space-y-2 rounded-2xl bg-primary/10 px-4 py-3">
              <p className="text-sm font-semibold text-foreground">
                {t('settings.about.updateAvailable')}
              </p>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.about.updateAvailableHelp', {
                  current: updates?.current ?? version,
                  latest: updates?.latest ?? '',
                })}
              </p>
              {applySupported && onApply ? (
                <Button type="button" onClick={() => onApply()}>
                  {t('settings.about.install')}
                </Button>
              ) : onOpenUpdate ? (
                <Button type="button" onClick={onOpenUpdate}>
                  {t('settings.about.showInstructions')}
                </Button>
              ) : null}
            </div>
          )}
          {applySupported && onAutoUpdate ? (
            <div className="mx-auto max-w-sm flex items-start gap-3 rounded-md border border-border/60 p-3 text-left">
              <Checkbox
                id="about-auto-update"
                checked={updates?.auto_update === true}
                onCheckedChange={(checked) => onAutoUpdate(checked === true)}
                className="mt-0.5"
              />
              <div className="space-y-1">
                <Label htmlFor="about-auto-update">{t('settings.about.autoUpdate')}</Label>
                <p className="text-[0.8125rem] text-muted-foreground">
                  {t('settings.about.autoUpdateHelp')}
                </p>
              </div>
            </div>
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
