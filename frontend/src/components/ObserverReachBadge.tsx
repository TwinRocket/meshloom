import { Ear } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ObserverReachCountState } from '../types';
import { handleKeyboardActivate } from '../utils/a11y';
import { cn } from '@/lib/utils';

interface ObserverReachBadgeProps {
  state?: ObserverReachCountState;
  variant: 'header' | 'inline';
  onOpen: () => void;
  className?: string;
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

function RollingCount({ value }: { value: number }) {
  const target = String(value);
  const [shown, setShown] = useState(target);
  const [incoming, setIncoming] = useState<string | null>(null);

  useEffect(() => {
    if (target === (incoming ?? shown)) return;
    if (prefersReducedMotion()) {
      setShown(target);
      setIncoming(null);
      return;
    }
    setIncoming(target);
  }, [target, incoming, shown]);

  const from = shown;
  const to = incoming ?? shown;
  const rolling = incoming != null && incoming !== shown;
  const width = Math.max(from.length, to.length);
  const fromPad = from.padStart(width, ' ');
  const toPad = to.padStart(width, ' ');

  return (
    <span
      className="inline-flex tabular-nums"
      data-testid="observer-reach-count"
      aria-hidden="true"
    >
      {toPad.split('').map((ch, index) => {
        const fromCh = fromPad[index] ?? ' ';
        const key = width - index;
        if (!rolling || fromCh === ch) {
          return (
            <span key={key} className="inline-block w-[1ch] text-center">
              {ch === ' ' ? '\u00a0' : ch}
            </span>
          );
        }
        return (
          <span
            key={key}
            className="relative inline-block h-[1em] w-[1ch] overflow-hidden align-baseline"
          >
            <span
              className="rolling-count-reel flex flex-col"
              onAnimationEnd={() => {
                setShown(to);
                setIncoming(null);
              }}
            >
              <span className="text-center">{fromCh === ' ' ? '\u00a0' : fromCh}</span>
              <span className="text-center">{ch === ' ' ? '\u00a0' : ch}</span>
            </span>
          </span>
        );
      })}
    </span>
  );
}

export function ObserverReachBadge({ state, variant, onOpen, className }: ObserverReachBadgeProps) {
  const { t } = useTranslation();
  if (state?.status !== 'ok' || state.count <= 0) {
    return null;
  }
  const count = state.count;
  const label = t('messageList.observerReachAria', { count });

  return (
    <span
      className={cn(
        'inline-flex items-baseline gap-0.5 cursor-pointer hover:text-primary hover:underline',
        variant === 'header'
          ? 'font-normal text-muted-foreground ml-1 text-[0.6875rem]'
          : 'text-[0.625rem] text-muted-foreground ml-1',
        className
      )}
      role="button"
      tabIndex={0}
      data-testid="observer-reach-badge"
      onKeyDown={handleKeyboardActivate}
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
      title={label}
      aria-label={label}
    >
      <Ear className="h-[1em] w-[1em] shrink-0 self-center" aria-hidden="true" />
      <RollingCount value={count} />
    </span>
  );
}
