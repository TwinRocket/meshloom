import { useCallback, useEffect, useRef, useState } from 'react';
import { Globe, Image as ImageIcon, Loader2, Lock, ShieldAlert } from 'lucide-react';
import { useDropzone, type FileRejection } from 'react-dropzone';
import { useTranslation } from 'react-i18next';
import { api, formatApiError, isAbortError } from '../api';
import {
  refreshShareStatus,
  useShareStatus,
  useShareStatusError,
} from '../stores/shareStatusStore';
import type { ShareFile, ShareSettingsUpdate } from '../types';
import type { ShareTarget } from '../utils/channelClass';
import { estimatedFileMessageBytes, fileMessage, fitsLimit } from '../utils/fileCaption';
import {
  IMAGE_ACCEPT,
  ImageRefusedError,
  MAX_SOURCE_BYTES,
  classifySource,
  downscaleImage,
  type ImageRefusal,
  type PreparedImage,
} from '../utils/imageDownscale';
import { byteLength } from '../utils/share';
import {
  getSavedShareMode,
  setSavedShareMode,
  shareFileName,
  type ShareMode,
} from '../utils/shareMode';
import { cn } from '@/lib/utils';
import { ShareConsentDialog } from './settings/SettingsShareSection';
import { Button } from './ui/button';
import { toast } from './ui/sonner';

/** A file handed to the tab from outside (the textarea's paste handler). `key` makes a repeat new. */
export interface IncomingFile {
  file: File;
  key: number;
}

interface ComposerFileTabProps {
  /** Sends the message through the composer's normal path; resolves true when it went out. */
  onSend: (text: string) => Promise<boolean>;
  /** Largest message in bytes (exclusive) once the reply prefix is counted; null: no limit. */
  byteLimit: number | null;
  incoming: IncomingFile | null;
  /** Called once `incoming` is taken, so the owner drops it (reopening the tab must not replay it). */
  onIncomingConsumed?: () => void;
  disabled?: boolean;
  /**
   * The conversation's class (`utils/channelClass.ts`). Encryptable: a Public /
   * Encrypted selector; otherwise public only, with a one-line reason. Absent: public
   * only, no selector.
   */
  target?: ShareTarget | null;
}

type Phase = 'idle' | 'preparing' | 'ready' | 'uploading' | 'sending';

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

const TYPE_LABEL: Record<string, string> = {
  'image/jpeg': 'JPEG',
  'image/png': 'PNG',
  'image/webp': 'WebP',
  'image/gif': 'GIF',
};

export function ComposerFileTab({
  onSend,
  byteLimit,
  incoming,
  onIncomingConsumed,
  disabled,
  target = null,
}: ComposerFileTabProps) {
  const { t } = useTranslation();
  const status = useShareStatus();
  const statusError = useShareStatusError();
  const [phase, setPhase] = useState<Phase>('idle');
  const [prepared, setPrepared] = useState<PreparedImage | null>(null);
  const [refusal, setRefusal] = useState<ImageRefusal | null>(null);
  const [caption, setCaption] = useState('');
  const [progress, setProgress] = useState(0);
  const [consentOpen, setConsentOpen] = useState(false);
  const [consentBusy, setConsentBusy] = useState(false);
  // Kept after a successful upload so a failed send is retried without uploading again;
  // `key` is the mode and conversation it was sealed for, never reused for another.
  const [uploaded, setUploaded] = useState<{ file: ShareFile; key: string } | null>(null);
  const [sourceName, setSourceName] = useState<string | undefined>(undefined);
  const [savedMode, setSavedMode] = useState<ShareMode>(getSavedShareMode);
  const abortRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    if (!status) void refreshShareStatus();
  }, [status]);

  // Leaving the tab (or the composer) cancels a running upload.
  useEffect(
    () => () => {
      generationRef.current += 1;
      abortRef.current?.abort();
    },
    []
  );

  const reset = useCallback(() => {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase('idle');
    setPrepared(null);
    setUploaded(null);
    setProgress(0);
  }, []);

  const prepare = useCallback(
    async (file: File) => {
      reset();
      setRefusal(null);
      setSourceName(shareFileName(file.name));
      const generation = generationRef.current;
      const kind = classifySource(file);
      if (kind === 'heic' || kind === 'unsupported') {
        setRefusal(kind);
        return;
      }
      setPhase('preparing');
      try {
        const result = await downscaleImage(file);
        if (generation !== generationRef.current) return;
        setPrepared(result);
        setPhase('ready');
      } catch (err) {
        if (generation !== generationRef.current) return;
        setRefusal(err instanceof ImageRefusedError ? err.code : 'decode');
        setPhase('idle');
      }
    },
    [reset]
  );

  const consumed = useRef<IncomingFile | null>(null);
  useEffect(() => {
    if (!incoming || consumed.current === incoming) return;
    consumed.current = incoming;
    void prepare(incoming.file);
    onIncomingConsumed?.();
  }, [incoming, prepare, onIncomingConsumed]);

  const onDrop = useCallback(
    (accepted: File[], rejected: FileRejection[]) => {
      const file = accepted[0];
      if (file) {
        void prepare(file);
        return;
      }
      const first = rejected[0];
      if (!first) return;
      reset();
      const kind = classifySource(first.file);
      const tooLarge = first.errors.some((e) => e.code === 'file-too-large');
      setRefusal(kind === 'heic' ? 'heic' : tooLarge ? 'tooLarge' : 'unsupported');
    },
    [prepare, reset]
  );

  const busy = phase === 'preparing' || phase === 'uploading' || phase === 'sending';
  const shareOn = status?.enabled === true;
  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    accept: IMAGE_ACCEPT,
    multiple: false,
    maxSize: MAX_SOURCE_BYTES,
    noClick: true,
    noKeyboard: true,
    disabled: disabled || busy || !shareOn,
  });

  // Encryption only where it protects something (a private channel, a DM), and only
  // when the upload can name the conversation (a DM also needs the radio key).
  // Anything but a confirmed `true` (an answer without the field included) keeps it off.
  const dmKeyMissing = target?.class === 'dm' && status?.dm_encryption_available !== true;
  const encryptAvailable = !!target?.encryptable && !!target.conversation && !dmKeyMissing;
  const mode: ShareMode = encryptAvailable ? savedMode : 'public';
  const uploadKey = mode === 'encrypted' ? `encrypted:${target?.conversation?.id}` : 'public';

  const chooseMode = (next: ShareMode) => {
    if (busy || next === mode) return;
    setSavedMode(next);
    setSavedShareMode(next);
  };

  const host = status?.host ?? 'meshl.ink';
  const estimated = estimatedFileMessageBytes(host, caption);
  const captionFits = fitsLimit(estimated, byteLimit);
  const trimmedCaption = caption.trim();

  const send = async () => {
    if (!prepared || busy || disabled || !captionFits) return;
    const generation = generationRef.current;
    let file = uploaded?.key === uploadKey ? uploaded.file : null;
    if (!file) {
      const controller = new AbortController();
      abortRef.current = controller;
      setPhase('uploading');
      setProgress(0);
      try {
        const encrypted =
          mode === 'encrypted' && target?.conversation
            ? { encrypted: target.conversation, name: sourceName }
            : {};
        file = await api.uploadShareFile(prepared.blob, {
          signal: controller.signal,
          onProgress: (loaded, total) => {
            if (generation === generationRef.current && total > 0) setProgress(loaded / total);
          },
          ...encrypted,
        });
      } catch (err) {
        if (generation !== generationRef.current) return;
        abortRef.current = null;
        setPhase('ready');
        if (isAbortError(err)) return;
        toast.error(
          err instanceof Error && err.name !== 'ApiError'
            ? t('chat.fileTab.uploadFailed')
            : formatApiError(err, t)
        );
        // A refused write may mean sharing was switched off or banned meanwhile.
        void refreshShareStatus();
        return;
      }
      if (generation !== generationRef.current) return;
      abortRef.current = null;
      setUploaded({ file, key: uploadKey });
    }
    const message = fileMessage(file.text, trimmedCaption);
    // The real link can differ from the estimate: never truncate, let the user shorten the caption.
    if (!fitsLimit(byteLength(message), byteLimit)) {
      setPhase('ready');
      toast.error(t('chat.fileTab.captionTooLong'));
      return;
    }
    setPhase('sending');
    const sent = await onSend(message);
    if (generation !== generationRef.current) return;
    setPhase('ready');
    if (sent) reset();
  };

  const cancel = () => {
    if (phase === 'uploading') abortRef.current?.abort();
  };

  const confirmConsent = async () => {
    setConsentBusy(true);
    try {
      const update: ShareSettingsUpdate = { share_enabled: true, share_terms_accepted: true };
      await api.updateSettings(update);
      await refreshShareStatus();
      setConsentOpen(false);
    } catch (err) {
      toast.error(formatApiError(err, t));
      await refreshShareStatus();
    } finally {
      setConsentBusy(false);
    }
  };

  if (!status && statusError) {
    return (
      <div data-testid="file-tab" className="space-y-3 p-3">
        <p
          role="alert"
          data-testid="file-tab-status-error"
          className="text-[0.8125rem] text-destructive"
        >
          {t('chat.fileTab.statusError')}
        </p>
        <Button
          type="button"
          variant="outline"
          className="w-full"
          data-testid="file-tab-status-retry"
          onClick={() => void refreshShareStatus()}
        >
          {t('chat.fileTab.statusRetry')}
        </Button>
      </div>
    );
  }

  if (!status) {
    return (
      <div data-testid="file-tab" className="p-3 text-[0.8125rem] text-muted-foreground">
        {t('chat.fileTab.loading')}
      </div>
    );
  }

  if (!shareOn) {
    return (
      <div data-testid="file-tab" className="space-y-3 p-3">
        <p className="text-[0.8125rem] text-muted-foreground">
          {status.banned ? t('errors.share_banned') : t('chat.fileTab.off')}
        </p>
        {!status.banned && (
          <Button
            type="button"
            className="w-full"
            data-testid="file-tab-enable"
            disabled={disabled || status.unreachable}
            onClick={() => setConsentOpen(true)}
          >
            {t('chat.fileTab.enable')}
          </Button>
        )}
        <ShareConsentDialog
          status={status}
          open={consentOpen}
          busy={consentBusy}
          onOpenChange={setConsentOpen}
          onConfirm={() => void confirmConsent()}
        />
      </div>
    );
  }

  return (
    <div data-testid="file-tab" className="space-y-3 p-3">
      {target?.encryptable ? (
        <div className="space-y-1.5">
          <div
            role="radiogroup"
            aria-label={t('chat.fileTab.mode.label')}
            data-testid="file-tab-mode"
            className="grid grid-cols-2 gap-1 rounded-lg border border-border p-1"
          >
            {(['public', 'encrypted'] as const).map((value) => {
              const Icon = value === 'public' ? Globe : Lock;
              const checked = mode === value;
              return (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  data-testid={`file-tab-mode-${value}`}
                  disabled={busy || (value === 'encrypted' && !encryptAvailable)}
                  onClick={() => chooseMode(value)}
                  className={cn(
                    'flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[0.8125rem] font-medium disabled:cursor-not-allowed disabled:opacity-50',
                    checked ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                  )}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  {t(`chat.fileTab.mode.${value}`)}
                </button>
              );
            })}
          </div>
          <p data-testid="file-tab-mode-hint" className="text-xs text-muted-foreground">
            {!encryptAvailable
              ? dmKeyMissing
                ? t('chat.fileTab.mode.dmUnavailable')
                : t('chat.fileTab.mode.noConversationId')
              : t(`chat.fileTab.mode.${mode}Hint`)}
          </p>
        </div>
      ) : (
        target && (
          <p
            data-testid="file-tab-mode-clear"
            className="flex items-start gap-1.5 text-xs text-muted-foreground"
          >
            <Globe className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t(`chat.fileTab.mode.clear.${target.class}`)}
          </p>
        )
      )}
      <div
        {...getRootProps({
          className: cn(
            'flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-3 py-4 text-center',
            isDragActive && 'border-primary bg-muted'
          ),
        })}
      >
        <input {...getInputProps()} data-testid="file-tab-input" />
        <ImageIcon className="h-6 w-6 text-muted-foreground" aria-hidden="true" />
        <p className="text-[0.8125rem] text-muted-foreground">
          {isDragActive ? t('chat.fileTab.dropHere') : t('chat.fileTab.hint')}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="file-tab-choose"
          disabled={disabled || busy}
          onClick={open}
        >
          {t('chat.fileTab.choose')}
        </Button>
      </div>

      {refusal && (
        <p
          role="alert"
          data-testid="file-tab-refusal"
          className="flex items-start gap-2 text-[0.8125rem] text-destructive"
        >
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {t(`chat.fileTab.error.${refusal}`)}
        </p>
      )}

      {phase === 'preparing' && (
        <p className="flex items-center gap-2 text-[0.8125rem] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          {t('chat.fileTab.preparing')}
        </p>
      )}

      {prepared && (
        <div className="space-y-2">
          <p data-testid="file-tab-summary" className="text-[0.8125rem]">
            {t('chat.fileTab.summary', {
              type: TYPE_LABEL[prepared.type] ?? prepared.type,
              before: formatBytes(prepared.originalBytes),
              after: formatBytes(prepared.bytes),
              width: prepared.output.width,
              height: prepared.output.height,
            })}
          </p>
          <label className="block space-y-1">
            <span className="text-[0.8125rem] font-medium">{t('chat.fileTab.caption')}</span>
            <input
              type="text"
              value={caption}
              data-testid="file-tab-caption"
              disabled={busy}
              onChange={(event) => setCaption(event.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-base md:text-sm"
              aria-invalid={!captionFits}
            />
          </label>
          <p
            data-testid="file-tab-budget"
            className={cn(
              'text-xs tabular-nums',
              captionFits ? 'text-muted-foreground' : 'font-medium text-destructive'
            )}
          >
            {byteLimit === null
              ? t('chat.fileTab.bytes', { count: estimated })
              : t('chat.fileTab.budget', { used: estimated, limit: byteLimit - 1 })}
            {!captionFits && ` - ${t('chat.fileTab.captionTooLong')}`}
          </p>
        </div>
      )}

      {phase === 'uploading' && (
        <progress
          value={progress}
          max={1}
          data-testid="file-tab-progress"
          aria-label={t('chat.fileTab.uploading')}
          className="h-2 w-full"
        />
      )}

      {prepared && (
        <div className="flex gap-2">
          {phase === 'uploading' && (
            <Button type="button" variant="outline" data-testid="file-tab-cancel" onClick={cancel}>
              {t('chat.fileTab.cancel')}
            </Button>
          )}
          {phase !== 'uploading' && (
            <Button
              type="button"
              variant="ghost"
              data-testid="file-tab-clear"
              disabled={busy}
              onClick={() => {
                reset();
                setCaption('');
              }}
            >
              {t('chat.fileTab.remove')}
            </Button>
          )}
          <Button
            type="button"
            className="flex-1"
            data-testid="file-tab-send"
            disabled={disabled || busy || !captionFits}
            onClick={() => void send()}
          >
            {phase === 'uploading'
              ? t('chat.fileTab.uploading')
              : phase === 'sending'
                ? t('chat.sending')
                : t('chat.fileTab.send')}
          </Button>
        </div>
      )}
    </div>
  );
}
