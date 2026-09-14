import {
  useState,
  useCallback,
  useImperativeHandle,
  forwardRef,
  useRef,
  useEffect,
  useMemo,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import { MapPin, Plus, Send, Smile, Sticker, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { cn } from '@/lib/utils';
import { ComposerEmojiPicker } from './ComposerEmojiPicker';
import { GifPicker } from './GifPicker';
import {
  getTextReplaceEnabled,
  getTextReplaceMapJson,
  applyTextReplacements,
} from '../utils/textReplace';
import { loadConversationDraft, saveConversationDraft } from '../utils/conversationDrafts';
import { formatGif, formatLocation } from '../utils/meshcoreOpenPayloads';

// MeshCore message size limits (empirically determined from LoRa packet constraints)
// Direct delivery allows ~156 bytes; multi-hop requires buffer for path growth.
// Channels include "sender: " prefix in the encrypted payload.
// All limits are in bytes (UTF-8), not characters, since LoRa packets are byte-constrained.
const DM_HARD_LIMIT = 156; // Max bytes for direct delivery
const DM_WARNING_THRESHOLD = 140; // Conservative for multi-hop
const CHANNEL_HARD_LIMIT = 156; // Base byte limit before sender overhead
const CHANNEL_WARNING_THRESHOLD = 120; // Conservative for multi-hop
const CHANNEL_DANGER_BUFFER = 8; // Red zone starts this many bytes before hard limit

const textEncoder = new TextEncoder();
const RADIO_NO_RESPONSE_SNIPPET = 'no response was heard back';
/** Get UTF-8 byte length of a string (LoRa packets are byte-constrained, not character-constrained). */
function byteLen(s: string): number {
  return textEncoder.encode(s).length;
}

interface MessageInputProps {
  onSend: (text: string) => Promise<void>;
  disabled: boolean;
  placeholder?: string;
  /** Conversation type for character limit calculation */
  conversationType?: 'contact' | 'channel' | 'raw';
  /** Channel key or contact public key — drafts are keyed by getStateKey(type, id). */
  conversationId?: string;
  /** Sender name (radio name) for channel message limit calculation */
  senderName?: string;
  /** Radio advert coordinates used to send an Open location pin. */
  radioLat?: number | null;
  radioLon?: number | null;
}

type LimitState = 'normal' | 'warning' | 'danger' | 'error';

export interface MessageInputHandle {
  appendText: (text: string) => void;
  focus: () => void;
  startReply: (sender: string, quote: string) => void;
}

type DraftIdentity = { type: 'contact' | 'channel'; id: string };

function draftIdentityOf(
  conversationType?: 'contact' | 'channel' | 'raw',
  conversationId?: string
): DraftIdentity | null {
  if ((conversationType === 'contact' || conversationType === 'channel') && conversationId) {
    return { type: conversationType, id: conversationId };
  }
  return null;
}

export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(function MessageInput(
  {
    onSend,
    disabled,
    placeholder,
    conversationType,
    conversationId,
    senderName,
    radioLat,
    radioLon,
  },
  ref
) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [replyQuote, setReplyQuote] = useState<{ sender: string; quote: string } | null>(null);
  const [sending, setSending] = useState(false);
  const [composerPanel, setComposerPanel] = useState<'gif' | 'emoji' | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const moreMenuRef = useRef<HTMLDivElement>(null);
  const textRef = useRef(text);
  textRef.current = text;
  const draftIdentity = draftIdentityOf(conversationType, conversationId);
  const draftKeyRef = useRef<DraftIdentity | null>(null);

  /** Resize textarea to fit content, clamped between 1 row and ~6 rows. */
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    // Clamp: min 40px (≈1 row), max 160px (≈6 rows)
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  useImperativeHandle(ref, () => ({
    appendText: (appendedText: string) => {
      setText((prev) => {
        const next = prev + appendedText;
        if (draftIdentity) saveConversationDraft(draftIdentity.type, draftIdentity.id, next);
        return next;
      });
      textareaRef.current?.focus();
    },
    focus: () => {
      textareaRef.current?.focus();
    },
    startReply: (sender: string, quote: string) => {
      setReplyQuote({ sender, quote });
      const prefix = `@[${sender}] `;
      setText((prev) => {
        const next = prev.startsWith(prefix) ? prev : prefix + prev;
        if (draftIdentity) saveConversationDraft(draftIdentity.type, draftIdentity.id, next);
        return next;
      });
      textareaRef.current?.focus();
    },
  }));

  // Restore/save drafts when the conversation identity changes. Empty drafts delete the key.
  useEffect(() => {
    const previous = draftKeyRef.current;
    const next = draftIdentityOf(conversationType, conversationId);
    if (previous && (previous.type !== next?.type || previous.id !== next.id)) {
      saveConversationDraft(previous.type, previous.id, textRef.current);
    }
    draftKeyRef.current = next;
    if (!next) {
      setText('');
      setReplyQuote(null);
      setComposerPanel(null);
      setMoreOpen(false);
      return;
    }
    const loaded = loadConversationDraft(next.type, next.id);
    setText(loaded);
    textRef.current = loaded;
    setReplyQuote(null);
    setComposerPanel(null);
    setMoreOpen(false);
  }, [conversationType, conversationId]);

  useEffect(() => {
    return () => {
      const current = draftKeyRef.current;
      if (current) {
        saveConversationDraft(current.type, current.id, textRef.current);
      }
    };
  }, []);

  // Re-measure height whenever text changes (covers programmatic updates like appendText)
  useEffect(() => {
    autoResize();
  }, [text, autoResize]);

  // Calculate character limits based on conversation type
  const limits = useMemo(() => {
    if (conversationType === 'contact') {
      return {
        warningAt: DM_WARNING_THRESHOLD,
        dangerAt: DM_HARD_LIMIT, // Same as hard limit for DMs (no intermediate red zone)
        hardLimit: DM_HARD_LIMIT,
      };
    } else if (conversationType === 'channel') {
      // Channel hard limit = 156 bytes - senderName bytes - 2 (for ": " separator)
      const nameByteLen = senderName ? byteLen(senderName) : 10;
      const hardLimit = Math.max(1, CHANNEL_HARD_LIMIT - nameByteLen - 2);
      return {
        warningAt: CHANNEL_WARNING_THRESHOLD,
        dangerAt: Math.max(1, hardLimit - CHANNEL_DANGER_BUFFER),
        hardLimit,
      };
    }
    return null; // Raw/other - no limits
  }, [conversationType, senderName]);

  // UTF-8 byte length of the current text (LoRa packets are byte-constrained)
  const textByteLen = useMemo(() => byteLen(text), [text]);

  // Determine current limit state
  const { limitState, warningMessage } = useMemo((): {
    limitState: LimitState;
    warningMessage: string | null;
  } => {
    if (!limits) return { limitState: 'normal', warningMessage: null };

    if (textByteLen >= limits.hardLimit) {
      return { limitState: 'error', warningMessage: t('chat.tooLong') };
    }
    if (textByteLen >= limits.dangerAt) {
      return { limitState: 'danger', warningMessage: t('chat.multiHopWarn') };
    }
    if (textByteLen >= limits.warningAt) {
      return { limitState: 'warning', warningMessage: t('chat.multiHopWarn') };
    }
    return { limitState: 'normal', warningMessage: null };
  }, [textByteLen, limits, t]);

  const remaining = limits ? limits.hardLimit - textByteLen : 0;

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const trimmed = text.trim();
      if (!trimmed || sending || disabled || limitState === 'error') return;

      setSending(true);
      try {
        await onSend(trimmed);
        setText('');
        setReplyQuote(null);
        if (draftIdentity) {
          saveConversationDraft(draftIdentity.type, draftIdentity.id, '');
        }
      } catch (err) {
        console.error('Failed to send message:', err);
        const description = err instanceof Error ? err.message : t('chat.checkRadio');
        const isRadioNoResponse =
          err instanceof Error && err.message.toLowerCase().includes(RADIO_NO_RESPONSE_SNIPPET);
        toast.error(isRadioNoResponse ? t('chat.radioNoConfirm') : t('chat.failedToSend'), {
          description,
        });
        return;
      } finally {
        setSending(false);
      }
      // Refocus after React re-enables the textarea
      setTimeout(() => textareaRef.current?.focus(), 0);
    },
    [text, sending, disabled, limitState, onSend, draftIdentity, t]
  );

  const persistDraft = useCallback(
    (next: string) => {
      if (draftIdentity) {
        saveConversationDraft(draftIdentity.type, draftIdentity.id, next);
      }
    },
    [draftIdentity]
  );

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      const input = e.target;
      const raw = input.value;
      // Skip replacement during IME / dead-key composition to avoid garbling interim input
      if (!e.nativeEvent || (e.nativeEvent as InputEvent).isComposing) {
        setText(raw);
        persistDraft(raw);
        return;
      }
      if (getTextReplaceEnabled()) {
        const result = applyTextReplacements(
          raw,
          input.selectionStart ?? raw.length,
          getTextReplaceMapJson()
        );
        if (result) {
          setText(result.text);
          persistDraft(result.text);
          // Schedule cursor restore after React flushes the new value
          const pos = result.cursor;
          requestAnimationFrame(() => input.setSelectionRange(pos, pos));
          return;
        }
      }
      setText(raw);
      persistDraft(raw);
    },
    [persistDraft]
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit(e as unknown as FormEvent);
      }
      // Shift+Enter falls through naturally and inserts a newline
    },
    [handleSubmit]
  );

  const sendPayload = useCallback(
    async (wire: string) => {
      if (sending || disabled) return;
      const payload = replyQuote ? `@[${replyQuote.sender}] ${wire}` : wire;
      setSending(true);
      try {
        await onSend(payload);
        setReplyQuote(null);
        setComposerPanel(null);
      } catch (err) {
        console.error('Failed to send message:', err);
        const description = err instanceof Error ? err.message : t('chat.checkRadio');
        const isRadioNoResponse =
          err instanceof Error && err.message.toLowerCase().includes(RADIO_NO_RESPONSE_SNIPPET);
        toast.error(isRadioNoResponse ? t('chat.radioNoConfirm') : t('chat.failedToSend'), {
          description,
        });
      } finally {
        setSending(false);
      }
    },
    [disabled, onSend, replyQuote, sending, t]
  );

  const insertEmoji = useCallback(
    (emoji: string) => {
      const el = textareaRef.current;
      const start = el?.selectionStart ?? text.length;
      const end = el?.selectionEnd ?? text.length;
      const next = text.slice(0, start) + emoji + text.slice(end);
      setText(next);
      persistDraft(next);
      requestAnimationFrame(() => {
        const cursor = start + emoji.length;
        el?.focus();
        el?.setSelectionRange(cursor, cursor);
      });
    },
    [persistDraft, text]
  );

  const sendLocation = useCallback(() => {
    setMoreOpen(false);
    const wire = formatLocation(radioLat ?? 0, radioLon ?? 0, senderName || 'pin');
    if (!wire) {
      toast.error(t('share.noLocation'));
      return;
    }
    void sendPayload(wire);
  }, [radioLat, radioLon, sendPayload, senderName, t]);

  const hasShareableLocation =
    radioLat != null &&
    radioLon != null &&
    Number.isFinite(radioLat) &&
    Number.isFinite(radioLon) &&
    !(radioLat === 0 && radioLon === 0);

  useEffect(() => {
    if (!composerPanel) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (target && pickerRef.current?.contains(target)) return;
      setComposerPanel(null);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setComposerPanel(null);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [composerPanel]);

  useEffect(() => {
    if (!moreOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (target && moreMenuRef.current?.contains(target)) return;
      setMoreOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setMoreOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [moreOpen]);

  const canSubmit = text.trim().length > 0 && limitState !== 'error';

  // Show counter for messages (not raw).
  // Desktop: always visible. Mobile: only after the UTF-8 warning threshold.
  const showCharCounter = limits !== null;
  const showMobileCounterValue = limits !== null && textByteLen >= limits.warningAt;

  return (
    <form
      className="message-input-shell px-4 py-2.5 border-t border-border flex flex-col gap-1"
      onSubmit={handleSubmit}
      autoComplete="off"
    >
      {replyQuote && (
        <div className="flex items-start gap-2 rounded-md border border-border bg-muted/50 px-3 py-2">
          <div className="min-w-0 flex-1">
            <p className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
              {t('chat.replyTo', { sender: replyQuote.sender })}
            </p>
            <p className="text-[0.8125rem] text-muted-foreground truncate">{replyQuote.quote}</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8 flex-shrink-0"
            aria-label={t('chat.cancelReply')}
            onClick={() => setReplyQuote(null)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}
      <div ref={pickerRef} className="relative flex gap-2 items-end">
        {composerPanel === 'gif' && (
          <GifPicker
            onSelect={(gifId) => {
              void sendPayload(formatGif(gifId));
            }}
            onClose={() => setComposerPanel(null)}
            disabled={disabled || sending}
          />
        )}
        {composerPanel === 'emoji' && (
          <ComposerEmojiPicker
            onSelect={insertEmoji}
            onClose={() => setComposerPanel(null)}
            disabled={disabled || sending}
          />
        )}
        <div ref={moreMenuRef} className="relative flex-shrink-0 sm:contents">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-10 w-10 sm:hidden"
            aria-label={t('chat.more')}
            aria-expanded={moreOpen}
            data-testid="composer-more-trigger"
            disabled={disabled || sending}
            onClick={() => {
              setMoreOpen((open) => !open);
              setComposerPanel(null);
            }}
          >
            <Plus className="h-5 w-5" />
          </Button>
          <div
            className={cn(
              moreOpen
                ? 'absolute bottom-full left-0 z-20 mb-1 flex items-center gap-0.5 rounded-md border border-border bg-background p-1 shadow-md'
                : 'hidden',
              'sm:contents'
            )}
          >
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-10 w-10 flex-shrink-0"
              aria-label={t('chat.emoji')}
              aria-expanded={composerPanel === 'emoji'}
              data-testid="emoji-picker-trigger"
              disabled={disabled || sending}
              onClick={() => {
                setMoreOpen(false);
                setComposerPanel((open) => (open === 'emoji' ? null : 'emoji'));
              }}
            >
              <Smile className="h-5 w-5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-10 w-10 flex-shrink-0"
              aria-label={t('chat.gif')}
              aria-expanded={composerPanel === 'gif'}
              data-testid="gif-picker-trigger"
              disabled={disabled || sending}
              onClick={() => {
                setMoreOpen(false);
                setComposerPanel((open) => (open === 'gif' ? null : 'gif'));
              }}
            >
              <Sticker className="h-5 w-5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-10 w-10 flex-shrink-0"
              aria-label={t('share.shareLocation')}
              data-testid="share-location-trigger"
              disabled={disabled || sending || !hasShareableLocation}
              onClick={sendLocation}
            >
              <MapPin className="h-5 w-5" />
            </Button>
          </div>
        </div>
        <textarea
          ref={textareaRef}
          name="chat-message-input"
          aria-label={placeholder || t('chat.typeMessage')}
          data-lpignore="true"
          data-1p-ignore="true"
          data-bwignore="true"
          rows={1}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || t('chat.typeMessagePlaceholder')}
          disabled={disabled || sending}
          enterKeyHint="send"
          className={cn(
            'flex-1 min-w-0 resize-none overflow-y-auto',
            // A filled pill rather than an outlined box: the field is already
            // distinct from the surface behind it, so the outline was a line doing
            // nothing but drawing itself.
            'rounded-3xl border border-transparent bg-muted/60 px-4 py-2 text-base',
            'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            'disabled:cursor-not-allowed disabled:opacity-50 md:text-sm'
          )}
          style={{ minHeight: '40px', maxHeight: '160px' }}
        />
        {/* On a phone it appears with the text and not before: an empty field has
            nothing to send, and a permanent button takes width from the field while
            claiming to be the way to send — which the keyboard's return key already
            is. Desktop keeps it at all times, disabled when empty, because a pointer
            has no return key under its thumb. */}
        <Button
          type="submit"
          disabled={disabled || sending || !canSubmit}
          className={cn(
            'h-10 w-10 flex-shrink-0 px-0 sm:w-auto sm:px-4',
            !canSubmit && 'hidden md:inline-flex'
          )}
          aria-label={sending ? t('chat.sending') : t('chat.send')}
        >
          <Send className="h-4 w-4 sm:hidden" aria-hidden="true" />
          <span className="hidden sm:inline">{sending ? t('chat.sending') : t('chat.send')}</span>
        </Button>
      </div>
      {showCharCounter && (
        <>
          <div className="hidden sm:flex items-center justify-end gap-2 text-xs">
            <span
              className={cn(
                'tabular-nums',
                limitState === 'error' || limitState === 'danger'
                  ? 'text-destructive font-medium'
                  : limitState === 'warning'
                    ? 'text-warning'
                    : 'text-muted-foreground'
              )}
            >
              {textByteLen}/{limits!.hardLimit}
              {remaining < 0 && ` (${remaining})`}
            </span>
            {warningMessage && (
              <span className={cn(limitState === 'error' ? 'text-destructive' : 'text-warning')}>
                — {warningMessage}
              </span>
            )}
          </div>

          {(showMobileCounterValue || warningMessage) && (
            <div className="flex sm:hidden items-center justify-end gap-2 text-xs">
              {showMobileCounterValue && (
                <span
                  className={cn(
                    'tabular-nums',
                    limitState === 'error' || limitState === 'danger'
                      ? 'text-destructive font-medium'
                      : limitState === 'warning'
                        ? 'text-warning'
                        : 'text-muted-foreground'
                  )}
                >
                  {textByteLen}/{limits!.hardLimit}
                  {remaining < 0 && ` (${remaining})`}
                </span>
              )}
              {warningMessage && (
                <span className={cn(limitState === 'error' ? 'text-destructive' : 'text-warning')}>
                  — {warningMessage}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </form>
  );
});
