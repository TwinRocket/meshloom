import { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { formatApiError } from '../../api';
import type {
  AppSettingsUpdate,
  SharePreviewPolicy,
  ShareShortenMode,
  ShareStatus,
} from '../../types';
import { loadShareStatus, refreshShareStatus, useShareStatus } from '../../stores/shareStatusStore';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { SettingsGroupHeader } from './settingsPrimitives';

const TERMS_URL = 'https://meshl.ink/terms';
const PRIVACY_URL = 'https://meshl.ink/privacy';

const SHORTEN_MODES: ShareShortenMode[] = ['auto', 'always', 'never'];
const PREVIEW_POLICIES: SharePreviewPolicy[] = ['always', 'private', 'never'];

function securityDocUrl(language: string): string {
  const lang = language.toLowerCase().startsWith('fr') ? 'fr' : 'en';
  return `https://meshloom.app/${lang}/docs/deep/security/`;
}

function ExternalLink({ href, children }: { href: string; children?: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-2 hover:text-primary transition-colors"
    >
      {children}
    </a>
  );
}

/**
 * Turning sharing on is a consent: the dialog says what leaves this instance, to
 * whom and for how long, and the switch only flips once the terms are accepted.
 * Cancelling, closing or leaving the box unticked changes nothing.
 */
function ShareConsentDialog({
  open,
  busy,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [accepted, setAccepted] = useState(false);

  // A fresh consent every time the dialog opens (adjusted during render, not in an effect).
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) setAccepted(false);
  }

  const links = {
    terms: <ExternalLink href={TERMS_URL} />,
    privacy: <ExternalLink href={PRIVACY_URL} />,
    security: <ExternalLink href={securityDocUrl(i18n.language)} />,
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent data-testid="share-consent-dialog">
        <DialogHeader>
          <DialogTitle>{t('settings.share.consent.title')}</DialogTitle>
          <DialogDescription>{t('settings.share.consent.intro')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-[0.8125rem]">
          <section className="space-y-1">
            <h4 className="font-medium">{t('settings.share.consent.sendingTitle')}</h4>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              <li>{t('settings.share.consent.sendingContent')}</li>
              <li>{t('settings.share.consent.sendingKey')}</li>
              <li>{t('settings.share.consent.sendingIp')}</li>
            </ul>
          </section>
          <section className="space-y-1">
            <h4 className="font-medium">{t('settings.share.consent.readingTitle')}</h4>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              <li>{t('settings.share.consent.readingId')}</li>
              <li>{t('settings.share.consent.readingIp')}</li>
            </ul>
          </section>
          <p className="text-muted-foreground">{t('settings.share.consent.who')}</p>
          <p className="text-muted-foreground">{t('settings.share.consent.retention')}</p>
          <p className="text-muted-foreground">
            <Trans i18nKey="settings.share.consent.docs" components={links} />
          </p>
        </div>
        <div className="flex items-start gap-2">
          <Checkbox
            id="share-accept-terms"
            checked={accepted}
            onCheckedChange={(value) => setAccepted(value === true)}
            disabled={busy}
          />
          <Label htmlFor="share-accept-terms" className="text-[0.8125rem] leading-snug">
            <Trans i18nKey="settings.share.consent.accept" components={links} />
          </Label>
        </div>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            {t('settings.share.consent.cancel')}
          </Button>
          <Button type="button" onClick={onConfirm} disabled={!accepted || busy}>
            {t('settings.share.consent.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function StatusLine({ status }: { status: ShareStatus }) {
  const { t, i18n } = useTranslation();
  const yes = t('settings.share.status.yes');
  const no = t('settings.share.status.no');
  return (
    <div data-testid="share-status" className="space-y-1 rounded-md border border-border/60 p-3">
      <ul className="space-y-0.5 text-[0.8125rem]">
        <li>
          {t('settings.share.status.enabled')}: {status.enabled ? yes : no}
        </li>
        <li>
          {t('settings.share.status.identity')}:{' '}
          {status.identity_ready ? yes : t('settings.share.status.identityPending')}
        </li>
        <li>
          {t('settings.share.status.dm')}: {status.dm_encryption_available ? yes : no}
        </li>
      </ul>
      {!status.dm_encryption_available && (
        <p data-testid="share-dm-unavailable" className="text-[0.8125rem] text-muted-foreground">
          <Trans
            i18nKey="settings.share.status.dmUnavailable"
            components={{ security: <ExternalLink href={securityDocUrl(i18n.language)} /> }}
          />
        </p>
      )}
    </div>
  );
}

export function SettingsShareSection({
  className,
  onSaveAppSettings,
}: {
  className?: string;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
}) {
  const { t } = useTranslation();
  const status = useShareStatus();
  const [loadError, setLoadError] = useState<string | null>(null);
  const [consentOpen, setConsentOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadShareStatus().then(
      () => setLoadError(null),
      (err) => setLoadError(formatApiError(err, t))
    );
    // t is stable per language; a language change must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async (update: AppSettingsUpdate): Promise<boolean> => {
    setBusy(true);
    try {
      await onSaveAppSettings(update);
      await refreshShareStatus();
      return true;
    } catch (err) {
      toast.error(formatApiError(err, t));
      await refreshShareStatus();
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (!status) {
    return (
      <div className={className}>
        <p className="text-[0.8125rem] text-muted-foreground">
          {loadError ?? t('settings.share.loading')}
        </p>
      </div>
    );
  }

  const handleToggle = (next: boolean) => {
    // Enabling always goes through the consent; disabling needs none.
    if (next) setConsentOpen(true);
    else void save({ share_enabled: false });
  };

  const handleConfirm = async () => {
    const ok = await save({ share_enabled: true });
    if (ok) setConsentOpen(false);
  };

  return (
    <div className={className}>
      <div className="space-y-1">
        <SettingsGroupHeader title={t('settings.share.title')} storedOn="server" instant />
        <p className="text-[0.8125rem] text-muted-foreground">{t('settings.share.intro')}</p>
      </div>

      <StatusLine status={status} />

      <div className="flex items-start gap-2">
        <Checkbox
          id="share-enabled"
          checked={status.enabled}
          onCheckedChange={(value) => handleToggle(value === true)}
          disabled={busy}
        />
        <div className="space-y-0.5">
          <Label htmlFor="share-enabled" className="text-[0.8125rem] font-medium">
            {t('settings.share.enable')}
          </Label>
          <p className="text-[0.8125rem] text-muted-foreground">{t('settings.share.enableHelp')}</p>
        </div>
      </div>

      <Separator />

      <fieldset className="space-y-1.5" disabled={busy}>
        <legend className="mb-1 text-[0.8125rem] font-medium">
          {t('settings.share.shorten.legend')}
        </legend>
        {SHORTEN_MODES.map((mode) => (
          <label key={mode} className="flex items-start gap-2 text-[0.8125rem]">
            <input
              type="radio"
              name="share-shorten-mode"
              value={mode}
              className="mt-0.5"
              checked={status.shorten_mode === mode}
              onChange={() => void save({ share_shorten_mode: mode })}
            />
            <span>
              <span className="font-medium">{t(`settings.share.shorten.${mode}`)}</span>
              <span className="block text-muted-foreground">
                {t(`settings.share.shorten.${mode}Help`)}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      <fieldset className="space-y-1.5" disabled={busy}>
        <legend className="mb-1 text-[0.8125rem] font-medium">
          {t('settings.share.preview.legend')}
        </legend>
        {PREVIEW_POLICIES.map((policy) => (
          <label key={policy} className="flex items-start gap-2 text-[0.8125rem]">
            <input
              type="radio"
              name="share-preview-policy"
              value={policy}
              className="mt-0.5"
              checked={status.preview_policy === policy}
              onChange={() => void save({ share_preview_policy: policy })}
            />
            <span>
              <span className="font-medium">{t(`settings.share.preview.${policy}`)}</span>
              <span className="block text-muted-foreground">
                {t(`settings.share.preview.${policy}Help`)}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      <ShareConsentDialog
        open={consentOpen}
        busy={busy}
        onOpenChange={setConsentOpen}
        onConfirm={() => void handleConfirm()}
      />
    </div>
  );
}
