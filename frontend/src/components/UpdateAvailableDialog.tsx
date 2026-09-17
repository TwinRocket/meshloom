import { useTranslation } from 'react-i18next';
import type { OssUpdateJobPhase, OssUpdateStatus } from '../types';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Label } from './ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

interface Props {
  open: boolean;
  updates: OssUpdateStatus | null;
  onClose: () => void;
  applying?: boolean;
  progressPercent?: number;
  progressPhase?: OssUpdateJobPhase | null;
  applyError?: string | null;
  onApply?: () => void;
  onAutoUpdate?: (enabled: boolean) => void;
}

function Recipe({ title, code }: { title: string; code: string }) {
  return (
    <div className="space-y-1.5">
      <h3 className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <pre className="overflow-x-auto rounded-xl bg-muted/40 px-4 py-3 font-mono text-[0.8125rem] leading-relaxed text-foreground">
        {code}
      </pre>
    </div>
  );
}

function VersionRows({ current, latest }: { current: string; latest: string }) {
  const { t } = useTranslation();
  return (
    <dl className="rounded-xl bg-muted/40 px-4 py-1">
      <div className="flex items-baseline justify-between gap-4 border-b border-border/30 py-2">
        <dt className="shrink-0 text-[0.6875rem] uppercase tracking-wider text-muted-foreground">
          {t('updates.current')}
        </dt>
        <dd className="min-w-0 break-words text-right text-sm">v{current}</dd>
      </div>
      <div className="flex items-baseline justify-between gap-4 py-2">
        <dt className="shrink-0 text-[0.6875rem] uppercase tracking-wider text-muted-foreground">
          {t('updates.latest')}
        </dt>
        <dd className="min-w-0 break-words text-right text-sm">v{latest}</dd>
      </div>
    </dl>
  );
}

function ProgressBar({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div
      className="h-2 overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
    >
      <div
        className="h-full bg-primary transition-[width] duration-300 ease-out"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export function UpdateAvailableDialog({
  open,
  updates,
  onClose,
  applying = false,
  progressPercent = 0,
  progressPhase = null,
  applyError = null,
  onApply,
  onAutoUpdate,
}: Props) {
  const { t } = useTranslation();
  const current = updates?.current ?? '—';
  const latest = updates?.latest ?? '—';
  const htmlUrl = updates?.html_url;
  const applySupported = updates?.apply_supported === true;
  const kind = updates?.install_kind;
  const showAddonHelp = kind === 'addon' && !applySupported;
  const showManualHelp = (kind === 'container' || kind === 'source') && !applySupported;
  const showRecipes = !applySupported && kind !== 'addon';
  const showInstall = applySupported && updates?.update_available === true && Boolean(onApply);
  const blocking = applying && !applyError;
  const showProgress = applying || Boolean(applyError);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !blocking) onClose();
      }}
    >
      <DialogContent className="sm:max-w-lg" hideCloseButton={blocking}>
        <DialogHeader>
          <DialogTitle>
            {applyError
              ? t('updates.failed')
              : applying
                ? t('updates.progressTitle')
                : t('updates.title')}
          </DialogTitle>
          <DialogDescription>
            {showProgress
              ? progressPhase
                ? t(`updates.phase.${progressPhase}`)
                : t('updates.progressTitle')
              : applySupported
                ? t('updates.helpApply')
                : t('updates.help')}
          </DialogDescription>
        </DialogHeader>

        <VersionRows current={current} latest={latest} />

        {showProgress ? (
          <div className="space-y-3">
            <ProgressBar percent={progressPercent} />
            {applyError ? (
              <p className="text-[0.8125rem] text-destructive">{applyError}</p>
            ) : (
              <p className="text-[0.8125rem] text-muted-foreground" aria-live="polite">
                {progressPhase ? t(`updates.phase.${progressPhase}`) : null}
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {showAddonHelp ? (
              <p className="text-[0.8125rem] text-muted-foreground">{t('updates.addonManual')}</p>
            ) : null}
            {showManualHelp ? (
              <p className="text-[0.8125rem] text-muted-foreground">{t('updates.manual')}</p>
            ) : null}
            {showRecipes ? (
              <>
                {/* English comments match README upgrade recipes; UI chrome is i18n. */}
                <Recipe
                  title={t('updates.packagesTitle')}
                  code={[
                    'sudo apt upgrade                          # Debian / Ubuntu',
                    'sudo dnf upgrade                          # Fedora / Rocky / Alma',
                  ].join('\n')}
                />
                <Recipe
                  title={t('updates.dockerTitle')}
                  code="sudo docker compose pull && sudo docker compose up -d"
                />
              </>
            ) : null}
            {applySupported && onAutoUpdate ? (
              <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
                <Checkbox
                  id="dialog-auto-update"
                  checked={updates?.auto_update === true}
                  onCheckedChange={(checked) => onAutoUpdate(checked === true)}
                  className="mt-0.5"
                />
                <div className="space-y-1">
                  <Label htmlFor="dialog-auto-update">{t('updates.autoUpdate')}</Label>
                  <p className="text-[0.8125rem] text-muted-foreground">
                    {t('updates.autoUpdateHelp')}
                  </p>
                </div>
              </div>
            ) : null}
          </div>
        )}

        <DialogFooter>
          {!blocking ? (
            <Button type="button" variant="outline" onClick={onClose}>
              {t('updates.close')}
            </Button>
          ) : null}
          {showInstall && !showProgress ? (
            <Button type="button" onClick={() => onApply?.()}>
              {t('updates.install')}
            </Button>
          ) : null}
          {!showProgress && htmlUrl ? (
            <Button asChild>
              <a href={htmlUrl} target="_blank" rel="noopener noreferrer">
                {t('updates.changelog')}
              </a>
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
