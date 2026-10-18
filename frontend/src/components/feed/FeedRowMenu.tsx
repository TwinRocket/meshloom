import { useEffect, useRef, type ReactNode } from 'react';
import { Copy, MessageSquare, User, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface FeedRowMenuProps {
  /** `Copy text` is hidden when there is no async clipboard (plain HTTP on the LAN). */
  canCopy: boolean;
  canOpenContact: boolean;
  onOpenConversation: () => void;
  onCopy: () => void;
  onOpenContact: () => void;
  /** `restoreFocus` is true for keyboard closes, so focus returns to the trigger. */
  onClose: (restoreFocus: boolean) => void;
}

function MenuItem({
  icon: Icon,
  label,
  onSelect,
}: {
  icon: LucideIcon;
  label: ReactNode;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-foreground hover:bg-accent focus-visible:bg-accent focus-visible:outline-hidden"
      onClick={onSelect}
    >
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      {label}
    </button>
  );
}

/**
 * The row menu, the pattern of `MessageActionMenu` (a `role="menu"` card under its
 * trigger). It takes focus when it opens, Escape or a press outside closes it, and
 * arrows move between items. No reply, react or resend here: the feed only reads.
 */
export function FeedRowMenu({
  canCopy,
  canOpenContact,
  onOpenConversation,
  onCopy,
  onOpenContact,
  onClose,
}: FeedRowMenuProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && !ref.current?.contains(target)) onClose(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [onClose]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose(true);
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const at = items.indexOf(document.activeElement as HTMLElement);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    items[(at + step + items.length) % items.length]?.focus();
  };

  return (
    <div
      ref={ref}
      role="menu"
      data-testid="feed-row-menu"
      className="absolute right-0 top-7 z-50 w-52 overflow-hidden rounded-xl border border-border bg-card py-1 text-sm shadow-lg"
      onKeyDown={handleKeyDown}
    >
      <MenuItem
        icon={MessageSquare}
        label={t('feed.row.openInConversation')}
        onSelect={onOpenConversation}
      />
      {canCopy && <MenuItem icon={Copy} label={t('feed.row.copy')} onSelect={onCopy} />}
      {canOpenContact && (
        <MenuItem icon={User} label={t('feed.row.contactDetails')} onSelect={onOpenContact} />
      )}
    </div>
  );
}
