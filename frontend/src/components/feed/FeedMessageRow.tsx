import { memo, useCallback, useEffect, useRef, useState, type Ref } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Message } from '../../types';
import { messageContainsMention } from '../../utils/messageParser';
import { RegionBadge } from '../../utils/messageText';
import { MessageBody } from '../message/MessageBody';
import { toast } from '../ui/sonner';
import { canCopyToClipboard, feedContactKey, type FeedRowActions } from './feedActions';
import type { FeedRowLabels } from './feedNames';
import { FeedRowMenu } from './FeedRowMenu';

interface FeedMessageRowProps {
  row: Message;
  labels: FeedRowLabels;
  time: { short: string; full: string };
  radioName?: string;
  actions?: FeedRowActions;
  /** False keeps a GIF as its text (display mode); a pending channel never loads it either. */
  remoteMedia?: boolean;
  /** Display mode: nothing to focus or click, the row is text on a screen. */
  readOnly?: boolean;
  /** The virtualizer's measuring ref, the item's index and its translateY offset. */
  measureRef?: Ref<HTMLDivElement>;
  index: number;
  offset: number;
}

const LONG_PRESS_MS = 500;
/** Clicks that land on these belong to them, not to the row. */
const INTERACTIVE = 'a,button,input,textarea,select,[role="menu"],[role="menuitem"]';

const nameButtonClass =
  'bidi-isolate min-w-0 truncate rounded-sm text-left hover:underline focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring';

/**
 * One message of the timeline: where it came from (channel or contact), who wrote it,
 * when it was received, then the body through `MessageBody`. A MeshCore Open reaction
 * whose target is not loaded shows as its emoji, rendered by `MessageBody` too. Names
 * come from the mesh, so they are bidi-isolated; the sender's abbreviated key is the
 * hover text that tells two homonyms apart.
 *
 * It is also the list item, so it owns the row's actions: click or Enter opens the
 * conversation at the message, the channel / contact name opens the conversation at its
 * bottom, the sender opens the contact sheet when a full key is known, and the `...`
 * button (long press or right click on touch) opens a menu with the same three.
 */
export const FeedMessageRow = memo(function FeedMessageRow({
  row,
  labels,
  time,
  radioName,
  actions,
  remoteMedia = true,
  readOnly = false,
  measureRef,
  index,
  offset,
}: FeedMessageRowProps) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const longPressRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressedRef = useRef(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const closeMenu = useCallback((restoreFocus: boolean) => {
    setMenuOpen(false);
    // The menu unmounts with focus inside it: without this focus falls to the page.
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  const contactKey = feedContactKey(row);
  // The radio's name in the app's `@[name]` form; a message we sent never marks itself.
  const mentionsUs = !row.outgoing && messageContainsMention(labels.content, radioName ?? null);
  const clearLongPress = useCallback(() => {
    if (longPressRef.current != null) clearTimeout(longPressRef.current);
    longPressRef.current = null;
  }, []);
  useEffect(() => clearLongPress, [clearLongPress]);

  const openMessage = () => actions?.onOpenMessage(row, labels.conversation);
  const openContact = () => {
    if (contactKey) actions?.onOpenContact(contactKey, row.type === 'CHAN');
  };
  const copy = () => {
    closeMenu(true);
    // A paste of the body without the "Sender: " prefix, as the conversation view copies.
    void navigator.clipboard.writeText(labels.content).then(
      () => toast.success(t('feed.row.copied')),
      () => toast.error(t('feed.row.copyFailed'))
    );
  };

  return (
    <div
      ref={measureRef}
      data-index={index}
      role="listitem"
      tabIndex={readOnly ? undefined : 0}
      data-mention={mentionsUs ? 'true' : undefined}
      className={`absolute left-0 top-0 w-full cursor-pointer border-b border-border/50 py-2 text-sm hover:bg-accent/30 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring${
        mentionsUs ? ' border-l-2 border-l-primary pl-2' : ''
      }`}
      style={{ transform: `translateY(${offset}px)` }}
      onClick={(event) => {
        if (longPressedRef.current) {
          longPressedRef.current = false;
          return;
        }
        if ((event.target as Element).closest(INTERACTIVE)) return;
        // Selecting text to copy it must not leave the feed.
        if (window.getSelection()?.toString()) return;
        openMessage();
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === 'Enter') {
          event.preventDefault();
          openMessage();
        } else if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
          event.preventDefault();
          setMenuOpen(true);
        }
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuOpen(true);
      }}
      // Also armed by a press on the name buttons; harmless, the click is only swallowed
      // after a real long press.
      onTouchStart={() => {
        clearLongPress();
        longPressRef.current = setTimeout(() => {
          longPressedRef.current = true;
          setMenuOpen(true);
        }, LONG_PRESS_MS);
      }}
      onTouchEnd={clearLongPress}
      onTouchMove={clearLongPress}
      onTouchCancel={clearLongPress}
    >
      <div className="relative flex items-baseline gap-2 pr-8 text-xs text-muted-foreground">
        <time dateTime={new Date(row.received_at * 1000).toISOString()} title={time.full}>
          {time.short}
        </time>
        {actions ? (
          <button
            type="button"
            className={`${nameButtonClass} font-medium text-foreground/80`}
            aria-label={t('feed.row.openConversationAria', { name: labels.conversation })}
            onClick={() => actions.onOpenConversation(row, labels.conversation)}
          >
            {labels.conversation}
          </button>
        ) : (
          <span className="bidi-isolate min-w-0 truncate font-medium text-foreground/80">
            {labels.conversation}
          </span>
        )}
        {labels.sender &&
          (actions && contactKey ? (
            <button
              type="button"
              className={nameButtonClass}
              title={labels.senderKey ?? undefined}
              aria-label={t('feed.row.contactAria', { name: labels.sender })}
              data-testid="feed-row-sender"
              onClick={openContact}
            >
              {labels.sender}
            </button>
          ) : (
            <span
              className="bidi-isolate min-w-0 truncate"
              title={labels.senderKey ?? undefined}
              data-testid="feed-row-sender"
            >
              {labels.sender}
            </span>
          ))}
        {mentionsUs && (
          // Visually hidden, not an aria-label: a label would replace the row's content for a
          // screen reader.
          <span className="sr-only">
            {t('feed.row.mentionAria', { name: labels.conversation })}
          </span>
        )}
        {row.region && <RegionBadge region={row.region} className="ml-0" />}
        {row.outgoing && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] uppercase tracking-wider">
            {t('feed.sent')}
          </span>
        )}
        {actions && (
          <>
            <button
              ref={triggerRef}
              type="button"
              className="absolute right-0 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full text-muted-foreground hover:bg-accent focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={t('feed.row.actions')}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              data-testid="feed-row-menu-button"
              onClick={() => setMenuOpen((open) => !open)}
            >
              <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
            </button>
            {menuOpen && (
              <FeedRowMenu
                canCopy={canCopyToClipboard()}
                canOpenContact={contactKey !== null}
                onOpenConversation={() => {
                  closeMenu(true);
                  openMessage();
                }}
                onCopy={copy}
                onOpenContact={() => {
                  closeMenu(true);
                  openContact();
                }}
                onClose={closeMenu}
              />
            )}
          </>
        )}
      </div>
      <MessageBody
        content={labels.content}
        radioName={radioName}
        remoteMedia={remoteMedia && !labels.pendingChannel}
      />
    </div>
  );
});
