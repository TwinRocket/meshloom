import { useTranslation } from 'react-i18next';
import { Button } from './ui/button';
import type { ServerLoginAttemptState } from '../utils/serverLoginState';
import { getServerLoginAttemptTone } from '../utils/serverLoginState';
import { cn } from '../lib/utils';

interface ServerLoginStatusBannerProps {
  attempt: ServerLoginAttemptState | null;
  loading: boolean;
  canRetryPassword: boolean;
  onRetryPassword: () => Promise<void> | void;
  onRetryBlank: () => Promise<void> | void;
  onReenterPassword: () => void;
  passwordRetryLabel?: string;
  blankRetryLabel?: string;
}

export function ServerLoginStatusBanner({
  attempt,
  loading,
  canRetryPassword,
  onRetryPassword,
  onRetryBlank,
  onReenterPassword,
  passwordRetryLabel,
  blankRetryLabel,
}: ServerLoginStatusBannerProps) {
  const { t } = useTranslation();
  const resolvedPasswordRetry = passwordRetryLabel ?? t('repeater.retryPassword');
  const resolvedBlankRetry = blankRetryLabel ?? t('repeater.retryExistingAccess');

  if (attempt?.outcome === 'confirmed') {
    return null;
  }

  // No attempt recorded is not a problem to report, it is how a session resumed
  // from this browser looks. Announcing "no attempt was recorded in this view" and
  // then offering to *retry* described the app's own bookkeeping and proposed
  // repeating something that never happened. Say what is true, and offer the one
  // action that applies: log in again.
  if (!attempt) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted/30 px-4 py-2">
        <p className="text-[0.8125rem] text-muted-foreground">{t('repeater.sessionResumed')}</p>
        <Button type="button" variant="outline" size="sm" onClick={onReenterPassword}>
          {t('repeater.logInAgain')}
        </Button>
      </div>
    );
  }

  const tone = getServerLoginAttemptTone(attempt);
  const toneClassName =
    tone === 'success'
      ? 'border-success/30 bg-success/10 text-success'
      : tone === 'warning'
        ? 'border-warning/30 bg-warning/10 text-warning'
        : tone === 'destructive'
          ? 'border-destructive/30 bg-destructive/10 text-destructive'
          : 'border-border bg-muted/40 text-foreground';

  return (
    <div className={cn('rounded-md border px-4 py-3', toneClassName)}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium">{attempt?.summary ?? t('repeater.noLoginAttempt')}</p>
          {attempt?.details && <p className="text-xs opacity-90">{attempt.details}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => void onRetryPassword()}
            disabled={loading || !canRetryPassword}
          >
            {resolvedPasswordRetry}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void onRetryBlank()}
            disabled={loading}
          >
            {resolvedBlankRetry}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={onReenterPassword}>
            {t('repeater.reenterPassword')}
          </Button>
        </div>
      </div>
    </div>
  );
}
