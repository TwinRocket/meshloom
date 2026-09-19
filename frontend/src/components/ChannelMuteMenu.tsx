import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { VolumeX } from 'lucide-react';

import { cn } from '../lib/utils';

export const MUTE_DURATIONS = [
  { seconds: 15 * 60, label: 'chatHeader.mute15m' },
  { seconds: 30 * 60, label: 'chatHeader.mute30m' },
  { seconds: 60 * 60, label: 'chatHeader.mute1h' },
  { seconds: 3 * 60 * 60, label: 'chatHeader.mute3h' },
  { seconds: 6 * 60 * 60, label: 'chatHeader.mute6h' },
  { seconds: 12 * 60 * 60, label: 'chatHeader.mute12h' },
  { seconds: 24 * 60 * 60, label: 'chatHeader.mute24h' },
  { seconds: -1, label: 'chatHeader.muteIndefinite' },
] as const;

interface ChannelMuteMenuProps {
  muted: boolean;
  mutedUntil?: number | null;
  onMute: (durationSeconds: number) => void;
}

function muteStatusLabel(
  muted: boolean,
  mutedUntil: number | null | undefined,
  format: (ts: number) => string,
  indefinite: string
): string | null {
  if (!muted) return null;
  if (mutedUntil && mutedUntil > Date.now() / 1000) {
    return format(mutedUntil);
  }
  return indefinite;
}

export function ChannelMuteMenu({ muted, mutedUntil, onMute }: ChannelMuteMenuProps) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const status = muteStatusLabel(
    muted,
    mutedUntil,
    (ts) =>
      t('chatHeader.mutedUntil', {
        time: new Date(ts * 1000).toLocaleTimeString(i18n.language, {
          hour: '2-digit',
          minute: '2-digit',
        }),
      }),
    t('chatHeader.mutedIndefinite')
  );

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded p-2 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => setOpen((value) => !value)}
        title={muted ? t('chatHeader.unmuteChannel') : t('chatHeader.muteChannel')}
        aria-label={muted ? t('chatHeader.unmuteChannel') : t('chatHeader.muteChannel')}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-pressed={muted}
      >
        <VolumeX
          className={cn('h-4 w-4', muted ? 'text-primary' : 'text-muted-foreground')}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-1 min-w-[12rem] rounded-md border border-border bg-background py-1 shadow-md"
        >
          {status && <p className="px-3 py-1.5 text-[0.6875rem] text-muted-foreground">{status}</p>}
          {muted && (
            <button
              type="button"
              role="menuitem"
              className="flex w-full px-3 py-1.5 text-left text-sm hover:bg-accent"
              onClick={() => {
                onMute(0);
                setOpen(false);
              }}
            >
              {t('chatHeader.unmuteChannel')}
            </button>
          )}
          <p className="px-3 pb-1 pt-1.5 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
            {t('chatHeader.muteFor')}
          </p>
          {MUTE_DURATIONS.map(({ seconds, label }) => (
            <button
              key={label}
              type="button"
              role="menuitem"
              className="flex w-full px-3 py-1.5 text-left text-sm hover:bg-accent"
              onClick={() => {
                onMute(seconds);
                setOpen(false);
              }}
            >
              {t(label)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
