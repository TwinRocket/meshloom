import { useTranslation } from 'react-i18next';
import type { OssUpdateStatus } from '../types';
import { Button } from './ui/button';
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

export function UpdateAvailableDialog({ open, updates, onClose }: Props) {
  const { t } = useTranslation();
  const current = updates?.current ?? '—';
  const latest = updates?.latest ?? '—';
  const htmlUrl = updates?.html_url;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('updates.title')}</DialogTitle>
          <DialogDescription>{t('updates.help')}</DialogDescription>
        </DialogHeader>

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

        <div className="space-y-4">
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
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {t('updates.close')}
          </Button>
          {htmlUrl ? (
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
