import { useEffect, useState } from 'react';
import { Download, Flag, Image as ImageIcon, Loader2, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api, ApiError, formatApiError, getApiErrorCode } from '../../api';
import { useCapabilities } from '../../auth/SessionProvider';
import { useBlockedSenders } from '../../contexts/BlockedSendersContext';
import {
  requestShareMeta,
  shareMetaKey,
  type ShareMetaEntry,
  useShareMetaEntry,
  useShareStatus,
} from '../../stores/shareMetaStore';
import {
  SHARE_REPORT_DETAILS_MAX,
  SHARE_REPORT_REASONS,
  type ShareMeta,
  type ShareReportReason,
} from '../../types';
import { isMessageBlocked, type BlockableMessage } from '../../utils/messageBlocking';
import {
  SHARE_INLINE_MAX_BYTES,
  shareMediaAutoLoads,
  sharePayloadLink,
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

export interface ShareImageOptions {
  /** Message text without the channel "Sender: " prefix. */
  content: string;
  /** The message, for `?message_id=` and the block lists. Absent: no id, never auto-loaded. */
  message?: ShareMessageContext;
  /** A DM or a private channel (key not derived from the name). */
  privateConversation: boolean;
  /** False from the parent forces the tap, whatever the policy. */
  remoteMedia: boolean;
}

/** The shared image of a message body and whether it may load (tapped or auto). */
export interface ShareImageState {
  id: string;
  /** The body is the encrypted form (`🔒 <link>`): its meta is asked for the message. */
  encrypted: boolean;
  load: boolean;
  tap: () => void;
}

/**
 * The shared image a message body carries (`📷 <service link>`) and its load gate.
 * It auto-loads under the preview policy (`always`; `private` in a DM or a private
 * channel), never with `remoteMedia` false, from a blocked sender, or before the
 * settings (and so the block lists) are loaded. Otherwise it waits for a tap.
 */
export function useShareImage({
  content,
  message,
  privateConversation,
  remoteMedia,
}: ShareImageOptions): ShareImageState | null {
  const status = useShareStatus();
  const { blockedKeys, blockedNames, loaded } = useBlockedSenders();
  const [tappedId, setTappedId] = useState<string | null>(null);
  const link = status?.enabled ? sharePayloadLink(content, status.hosts) : null;
  if (!link || !status) return null;
  const { id, encrypted } = link;
  const blocked = !loaded || (!!message && isMessageBlocked(message, blockedKeys, blockedNames));
  const auto = shareMediaAutoLoads({
    policy: status.preview_policy,
    privateConversation,
    remoteMedia: remoteMedia && !!message,
    blocked,
  });
  return { id, encrypted, load: auto || tappedId === id, tap: () => setTappedId(id) };
}

export interface SharePayloadProps {
  image: ShareImageState | null;
  message?: ShareMessageContext;
  /** False on a screen nobody interacts with (Feed display mode): no tap button. */
  interactive: boolean;
}

const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);

/**
 * A file the proxy serves inline as an image: a public one, or an encrypted one
 * this conversation decrypts (`decryptable` true; type and name come decrypted).
 */
function isShownImage(meta: ShareMeta): boolean {
  if (meta.kind !== 'file' || !meta.content_type || !IMAGE_TYPES.has(meta.content_type)) {
    return false;
  }
  return meta.mode === 'public' || (meta.mode === 'encrypted' && meta.decryptable === true);
}

const TYPE_LABEL: Record<string, string> = {
  'image/jpeg': 'JPEG',
  'image/png': 'PNG',
  'image/gif': 'GIF',
  'image/webp': 'WebP',
};

/** A file the service holds encrypted (the sender's `📷` or `🔒` prefix is only a claim). */
function isEncryptedFile(entry: ShareMetaEntry | null): boolean {
  return entry?.state === 'ready' && entry.meta.kind === 'file' && entry.meta.mode === 'encrypted';
}

function objectUrl(id: string, messageId: number | undefined): string {
  const path = `./api/share/${encodeURIComponent(id)}`;
  return messageId ? `${path}?message_id=${messageId}` : path;
}

/**
 * A shared image under a message, rendered after the text; the anchor stays. Nothing
 * is requested, the meta included, until the image may load (`useShareImage`).
 * Errors fall back to the raw link (nothing is drawn here), a 410 says the image
 * expired or was removed. An encrypted file shows a lock badge with its decrypted
 * name and type; one this conversation cannot decrypt (`decryptable` false) says it
 * is encrypted for another conversation, and one that cannot be read now (unknown,
 * failed or timed out) says so: the raw link stays and nothing spins.
 */
export function SharePayload({ image, message, interactive }: SharePayloadProps) {
  if (!image) return null;
  return (
    <SharePayloadCard
      key={image.id}
      id={image.id}
      encrypted={image.encrypted}
      load={image.load}
      onTap={image.tap}
      message={message}
      interactive={interactive}
    />
  );
}

function SharePayloadCard({
  id,
  encrypted,
  load,
  onTap,
  message,
  interactive,
}: {
  id: string;
  encrypted: boolean;
  load: boolean;
  onTap: () => void;
  message?: ShareMessageContext;
  interactive: boolean;
}) {
  const { t } = useTranslation();
  const { canSend } = useCapabilities();
  const [imageFailed, setImageFailed] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  // A public file's meta is the same in every message (shared with the anchor's hover);
  // an encrypted one is decrypted for this message's conversation. A `📷` body whose file
  // turns out encrypted is asked again with the message (the plain answer cannot decrypt).
  const plainEntry = useShareMetaEntry(load && !(encrypted && message) ? shareMetaKey(id) : null);
  const escalate = !!message && (encrypted || isEncryptedFile(plainEntry));
  const metaMessageId = escalate ? message?.id : undefined;
  const messageEntry = useShareMetaEntry(load && escalate ? shareMetaKey(id, metaMessageId) : null);
  const entry = escalate ? messageEntry : plainEntry;
  // An entry dropped while the card is mounted (null) is asked for again: never a spinner for good.
  const lost = load && entry === null;

  useEffect(() => {
    if (load) void requestShareMeta(id, metaMessageId);
  }, [load, id, metaMessageId, lost]);

  // A tap on "Try again": ask once more now, whatever the short failure memory says.
  const retry = () => {
    setImageFailed(false);
    void requestShareMeta(id, metaMessageId, { force: true });
  };

  if (!load) {
    if (!interactive) {
      return (
        <div
          className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground"
          data-testid="share-payload-placeholder"
        >
          <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />
          {t('sharePayload.notLoaded')}
        </div>
      );
    }
    return (
      <div className="mt-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="share-payload-tap"
          onClick={onTap}
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
    if (!entry.gone) {
      return encrypted ? <EncryptedNotice reason="unavailable" onRetry={() => retry()} /> : null;
    }
    return (
      <div className="mt-1 text-xs italic text-muted-foreground" data-testid="share-payload-gone">
        {t('sharePayload.gone')}
      </div>
    );
  }

  const { meta } = entry;
  const isEncrypted = meta.kind === 'file' && meta.mode === 'encrypted';
  if (isEncrypted && meta.decryptable === false) return <EncryptedNotice reason="elsewhere" />;
  if (isEncrypted && (meta.decryptable !== true || imageFailed)) {
    return <EncryptedNotice reason="unavailable" onRetry={() => retry()} />;
  }
  if (!isShownImage(meta) || imageFailed) return null;
  const url = objectUrl(id, message?.id);
  const tooLarge = typeof meta.size === 'number' && meta.size > SHARE_INLINE_MAX_BYTES;
  const typeLabel = meta.content_type ? (TYPE_LABEL[meta.content_type] ?? '') : '';

  return (
    <div className="mt-1 flex flex-col items-start gap-1" data-testid="share-payload">
      {isEncrypted && (
        <span
          className="inline-flex max-w-full items-center gap-1 text-[0.6875rem] text-muted-foreground"
          data-testid="share-payload-lock"
          title={t('sharePayload.encryptedHint')}
        >
          <Lock className="h-3 w-3 shrink-0 text-primary" aria-hidden="true" />
          <span className="font-medium">{t('sharePayload.encrypted')}</span>
          {meta.name && (
            <bdi className="min-w-0 truncate" data-testid="share-payload-name">
              {meta.name}
            </bdi>
          )}
          {typeLabel && <span>{typeLabel}</span>}
        </span>
      )}
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
      {canSend && (
        <>
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
        </>
      )}
    </div>
  );
}

/** Why an encrypted file is not shown: sealed for another conversation, or unreadable now. */
function EncryptedNotice({
  reason,
  onRetry,
}: {
  reason: 'elsewhere' | 'unavailable';
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="mt-1 inline-flex flex-wrap items-center gap-1.5 text-xs italic text-muted-foreground"
      data-testid={`share-payload-encrypted-${reason}`}
    >
      <Lock className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {reason === 'elsewhere'
        ? t('sharePayload.encryptedElsewhere')
        : t('sharePayload.encryptedUnavailable')}
      {reason === 'unavailable' && onRetry && (
        <button
          type="button"
          data-testid="share-payload-retry"
          onClick={onRetry}
          className="not-italic underline hover:text-foreground"
        >
          {t('sharePayload.retry')}
        </button>
      )}
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
      const code = err instanceof ApiError ? getApiErrorCode(err.detail)?.code : undefined;
      // The service allows a few reports per instance key and day, not the share quota.
      toast.error(
        code === 'share_quota_key' ? t('sharePayload.report.quota') : formatApiError(err, t)
      );
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
