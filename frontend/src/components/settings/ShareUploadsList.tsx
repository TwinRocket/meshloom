import { useCallback, useEffect, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
import type { ShareMineObject } from '../../types';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { toast } from '../ui/sonner';

/** `1536` → `1.5 KB` in the reader's language (decimal units, like the service's limits). */
export function formatShareBytes(bytes: number, language: string): string {
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : 1;
  return `${new Intl.NumberFormat(language, { maximumFractionDigits: digits }).format(value)} ${units[unit]}`;
}

function objectSize(object: ShareMineObject): number | null {
  if (object.size !== null) return object.size;
  if (object.size_kib !== null) return object.size_kib * 1024;
  return null;
}

function contactUrl(host: string, language: string): string {
  const lang = language.toLowerCase().startsWith('fr') ? 'fr' : 'en';
  return `https://${host}/legal?lang=${lang}`;
}

/**
 * "My uploads": what this instance posted (`GET /api/share/mine`, member+). Blocked rows
 * are greyed, name their category and link the service's contact page; they offer no
 * delete (the service refuses it). Active rows delete after a confirm
 * (`DELETE /api/share/{id}`, which also evicts this instance's cached copy).
 * The parent renders it only while sharing is on: off, the server never contacts the
 * service and would answer `share_disabled`.
 */
export function ShareUploadsList({ host }: { host: string }) {
  const { t, i18n } = useTranslation();
  const [objects, setObjects] = useState<ShareMineObject[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<ShareMineObject | null>(null);
  const [deleting, setDeleting] = useState(false);
  // A late page must not overwrite a newer refresh.
  const generation = useRef(0);

  // State changes only in the promise callbacks (the first load runs from an effect).
  const fetchPage = useCallback(
    (cursor?: string) => {
      const mine = ++generation.current;
      api.listShareMine(cursor).then(
        (page) => {
          if (mine !== generation.current) return;
          setObjects((previous) =>
            cursor && previous ? [...previous, ...page.objects] : page.objects
          );
          setNextCursor(page.next_cursor);
          setError(null);
          setLoading(false);
        },
        (err) => {
          if (mine !== generation.current) return;
          setError(formatApiError(err, t));
          setLoading(false);
        }
      );
    },
    [t]
  );

  const load = (cursor?: string) => {
    setLoading(true);
    fetchPage(cursor);
  };

  useEffect(() => {
    fetchPage();
    // A language change must not refetch (t changes with it).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const confirmDelete = async () => {
    if (!pending) return;
    setDeleting(true);
    try {
      await api.deleteShare(pending.id);
      setObjects((previous) => previous?.filter((o) => o.id !== pending.id) ?? previous);
      toast.success(t('settings.share.uploads.deleted'));
      setPending(null);
    } catch (err) {
      toast.error(formatApiError(err, t));
      setPending(null);
      // A blocked or vanished object changed state on the service: show what it says now.
      load();
    } finally {
      setDeleting(false);
    }
  };

  const dateFormat = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' });
  const unknown = t('settings.share.uploads.unknown');

  const kindLabel = (o: ShareMineObject) =>
    o.kind === 'link'
      ? t('settings.share.uploads.kind.link')
      : o.mode === 'encrypted'
        ? t('settings.share.uploads.kind.encrypted')
        : t('settings.share.uploads.kind.file');

  const whatLabel = (o: ShareMineObject) =>
    (o.kind === 'link' ? o.target_host : o.content_type) ?? unknown;

  const sizeLabel = (o: ShareMineObject) => {
    const size = objectSize(o);
    return size === null ? unknown : formatShareBytes(size, i18n.language);
  };

  const expiresLabel = (o: ShareMineObject) =>
    o.expires_at === null
      ? t('settings.share.uploads.never')
      : dateFormat.format(new Date(o.expires_at * 1000));

  return (
    <section data-testid="share-uploads" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[0.8125rem] font-medium">{t('settings.share.uploads.title')}</h4>
        <Button type="button" variant="outline" size="sm" onClick={() => load()} disabled={loading}>
          {t('settings.share.uploads.refresh')}
        </Button>
      </div>
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.share.uploads.intro')}</p>

      {error && (
        <p
          role="alert"
          data-testid="share-uploads-error"
          className="text-[0.8125rem] text-destructive"
        >
          {t('settings.share.uploads.error', { error })}
        </p>
      )}

      {objects === null && !error && (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.share.uploads.loading')}
        </p>
      )}

      {objects !== null && objects.length === 0 && (
        <p data-testid="share-uploads-empty" className="text-[0.8125rem] text-muted-foreground">
          {t('settings.share.uploads.empty')}
        </p>
      )}

      {objects !== null && objects.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[0.8125rem]">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-2 font-medium">
                  {t('settings.share.uploads.columns.kind')}
                </th>
                <th className="py-1 pr-2 font-medium">
                  {t('settings.share.uploads.columns.what')}
                </th>
                <th className="py-1 pr-2 font-medium">
                  {t('settings.share.uploads.columns.size')}
                </th>
                <th className="py-1 pr-2 font-medium">
                  {t('settings.share.uploads.columns.expires')}
                </th>
                <th className="py-1 pr-2 font-medium">
                  {t('settings.share.uploads.columns.status')}
                </th>
                <th className="py-1 font-medium">
                  <span className="sr-only">{t('settings.share.uploads.columns.actions')}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {objects.map((o) => {
                const blocked = o.status === 'blocked';
                return (
                  <tr
                    key={o.id}
                    data-testid={`share-upload-${o.id}`}
                    data-status={o.status}
                    className={
                      blocked
                        ? 'border-t border-border/60 text-muted-foreground opacity-60'
                        : 'border-t border-border/60'
                    }
                  >
                    <td className="py-1 pr-2">{kindLabel(o)}</td>
                    <td className="py-1 pr-2 break-all">{whatLabel(o)}</td>
                    <td className="py-1 pr-2 whitespace-nowrap">{sizeLabel(o)}</td>
                    <td className="py-1 pr-2 whitespace-nowrap">{expiresLabel(o)}</td>
                    <td className="py-1 pr-2">
                      {blocked ? (
                        <span className="space-y-0.5">
                          <span className="block">
                            {t('settings.share.uploads.status.blocked', {
                              category: t(
                                `sharePayload.report.reasons.${o.blocked_category ?? 'other'}`
                              ),
                            })}
                          </span>
                          <span className="block">
                            <Trans
                              i18nKey="settings.share.uploads.blockedHelp"
                              components={{
                                contact: (
                                  <a
                                    href={contactUrl(host, i18n.language)}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="underline underline-offset-2"
                                  />
                                ),
                              }}
                            />
                          </span>
                        </span>
                      ) : (
                        t('settings.share.uploads.status.active')
                      )}
                    </td>
                    <td className="py-1 text-right">
                      {!blocked && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={t('settings.share.uploads.deleteLabel', { id: o.id })}
                          onClick={() => setPending(o)}
                        >
                          {t('settings.share.uploads.delete')}
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {nextCursor && objects !== null && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => load(nextCursor)}
          disabled={loading}
        >
          {t('settings.share.uploads.more')}
        </Button>
      )}

      <Dialog
        open={pending !== null}
        onOpenChange={(open) => !open && !deleting && setPending(null)}
      >
        <DialogContent data-testid="share-delete-dialog">
          <DialogHeader>
            <DialogTitle>{t('settings.share.uploads.confirm.title')}</DialogTitle>
            <DialogDescription>{t('settings.share.uploads.confirm.body')}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => setPending(null)}
              disabled={deleting}
            >
              {t('settings.share.uploads.confirm.cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void confirmDelete()}
              disabled={deleting}
            >
              {t('settings.share.uploads.confirm.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
