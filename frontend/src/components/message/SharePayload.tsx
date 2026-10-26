import { useEffect, useState } from 'react';
import { Download, Flag, Image as ImageIcon, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
import { useBlockedSenders } from '../../contexts/BlockedSendersContext';
import { requestShareMeta, useShareMetaEntry, useShareStatus } from '../../stores/shareMetaStore';
import {
  SHARE_REPORT_DETAILS_MAX,
  SHARE_REPORT_REASONS,
  type ShareMeta,
  type ShareReportReason,
} from '../../types';
import { isMessageBlocked, type BlockableMessage } from '../../utils/messageBlocking';
import {
  SHARE_INLINE_MAX_BYTES,
  shareImageId,
  shareMediaAutoLoads,
} from '../../utils/sharePayload';
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

/** What a message tells the shared image about where it was received. */
export interface ShareMessageContext extends BlockableMessage {
  id: number;
}

export interface SharePayloadProps {
  /** Message text without the channel "Sender: " prefix. */
  content: string;
  /** The message, for `?message_id=` and the block lists. Absent: no id, never blocked. */
  message?: ShareMessageContext;
  /** A DM or a private channel (key not derived from the name). */
  privateConversation: boolean;
  /** False from the parent forces the tap, whatever the policy. */
  remoteMedia: boolean;
}

const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);

/** A public image file the proxy serves inline; anything else stays a plain link here. */
function isPublicImage(meta: ShareMeta): boolean {
  return (
    meta.kind === 'file' &&
    meta.mode === 'public' &&
    !!meta.content_type &&
    IMAGE_TYPES.has(meta.content_type)
  );
}

function objectUrl(id: string, messageId: number | undefined): string {
  const path = `./api/share/${encodeURIComponent(id)}`;
  return messageId ? `${path}?message_id=${messageId}` : path;
}

/**
 * A shared image under a message (`📷 <service link>`), rendered after the text; the
 * anchor stays. Nothing is requested, the meta included, until the user taps, unless
 * the preview policy lets it load: `always`; `private` in a DM or a private channel;
 * never with `remoteMedia` false or from a blocked sender. Errors fall back to the raw
 * link (nothing is drawn here), a 410 says the image expired or was removed.
 */
export function SharePayload({
  content,
  message,
  privateConversation,
  remoteMedia,
}: SharePayloadProps) {
  const status = useShareStatus();
  const id = status?.enabled ? shareImageId(content, status.hosts) : null;
  if (!id || !status) return null;
  return (
    <SharePayloadCard
      key={id}
      id={id}
      policy={status.preview_policy}
      message={message}
      privateConversation={privateConversation}
      remoteMedia={remoteMedia}
    />
  );
}

function SharePayloadCard({
  id,
  policy,
  message,
  privateConversation,
  remoteMedia,
}: {
  id: string;
  policy: Parameters<typeof shareMediaAutoLoads>[0]['policy'];
  message?: ShareMessageContext;
  privateConversation: boolean;
  remoteMedia: boolean;
}) {
  const { t } = useTranslation();
  const { blockedKeys, blockedNames } = useBlockedSenders();
  const blocked = !!message && isMessageBlocked(message, blockedKeys, blockedNames);
  const auto = shareMediaAutoLoads({ policy, privateConversation, remoteMedia, blocked });
  const [tapped, setTapped] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const load = auto || tapped;
  const entry = useShareMetaEntry(load ? id : null);

  useEffect(() => {
    if (load) void requestShareMeta(id);
  }, [load, id]);

  if (!load) {
    return (
      <div className="mt-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="share-payload-tap"
          onClick={() => setTapped(true)}
          title={t('sharePayload.tapToLoadHint')}
        >
          <ImageIcon className="h-4 w-4" aria-hidden="true" />
          {t('sharePayload.tapToLoad')}
        </Button>
      </div>
    );
  }

  if (!entry || entry.state === 'loading') {
    return (
      <div
        className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground"
        data-testid="share-payload-loading"
        role="status"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        {t('sharePayload.loading')}
      </div>
    );
  }

  if (entry.state === 'missing') {
    if (!entry.gone) return null;
    return (
      <div className="mt-1 text-xs italic text-muted-foreground" data-testid="share-payload-gone">
        {t('sharePayload.gone')}
      </div>
    );
  }

  const { meta } = entry;
  if (!isPublicImage(meta) || imageFailed) return null;
  const url = objectUrl(id, message?.id);
  const tooLarge = typeof meta.size === 'number' && meta.size > SHARE_INLINE_MAX_BYTES;

  return (
    <div className="mt-1 flex flex-col items-start gap-1" data-testid="share-payload">
      {tooLarge ? (
        <a
          href={url}
          download
          data-testid="share-payload-download"
          className="inline-flex items-center gap-2 rounded-md border border-border bg-muted/40 px-2.5 py-1.5 hover:bg-muted/70"
        >
          <Download className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <span className="min-w-0">
            <span className="block text-sm font-medium leading-tight">
              {t('sharePayload.download')}
            </span>
            <span className="block text-[0.6875rem] text-muted-foreground">
              {t('sharePayload.tooLarge', {
                size: Math.round(((meta.size ?? 0) / (1024 * 1024)) * 10) / 10,
              })}
            </span>
          </span>
        </a>
      ) : (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block"
          title={t('sharePayload.openImage')}
        >
          <img
            src={url}
            alt={t('sharePayload.imageAlt')}
            data-testid="share-payload-image"
            loading="lazy"
            onError={() => setImageFailed(true)}
            className="max-w-[240px] max-h-[240px] rounded-md"
          />
        </a>
      )}
      <button
        type="button"
        data-testid="share-payload-report"
        onClick={() => setReportOpen(true)}
        className="inline-flex items-center gap-1 text-[0.6875rem] text-muted-foreground hover:text-foreground"
      >
        <Flag className="h-3 w-3" aria-hidden="true" />
        {t('sharePayload.report.button')}
      </button>
      <ShareReportDialog id={id} open={reportOpen} onOpenChange={setReportOpen} />
    </div>
  );
}

/** Report a shared object to the service, through the instance (signed with its key). */
export function ShareReportDialog({
  id,
  open,
  onOpenChange,
}: {
  id: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState<ShareReportReason>('illegal');
  const [details, setDetails] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const trimmed = details.trim();
      await api.reportShare(id, trimmed ? { reason, details: trimmed } : { reason });
      toast.success(t('sharePayload.report.sent'));
      setDetails('');
      onOpenChange(false);
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent data-testid="share-report-dialog">
        <DialogHeader>
          <DialogTitle>{t('sharePayload.report.title')}</DialogTitle>
          <DialogDescription>{t('sharePayload.report.description')}</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('sharePayload.report.reason')}</span>
            <select
              data-testid="share-report-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value as ShareReportReason)}
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            >
              {SHARE_REPORT_REASONS.map((value) => (
                <option key={value} value={value}>
                  {t(`sharePayload.report.reasons.${value}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('sharePayload.report.details')}</span>
            <textarea
              data-testid="share-report-details"
              value={details}
              maxLength={SHARE_REPORT_DETAILS_MAX}
              onChange={(event) => setDetails(event.target.value)}
              rows={3}
              className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
            />
          </label>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => onOpenChange(false)}
            >
              {t('sharePayload.report.cancel')}
            </Button>
            <Button type="submit" disabled={busy} data-testid="share-report-submit">
              {t('sharePayload.report.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
