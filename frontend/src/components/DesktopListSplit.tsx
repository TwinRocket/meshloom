import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';

import {
  clampDesktopSplitWidth,
  DESKTOP_SPLIT_DEFAULT,
  DESKTOP_SPLIT_MAX,
  DESKTOP_SPLIT_MIN,
  desktopSplitBucket,
  getSavedDesktopSplitWidth,
  setSavedDesktopSplitWidth,
} from '../utils/desktopSplitPreference';

interface Props {
  children: ReactNode;
}

export function DesktopListSplit({ children }: Props) {
  const { t } = useTranslation();
  const [width, setWidth] = useState(() => getSavedDesktopSplitWidth(window.innerWidth));
  const widthRef = useRef(width);
  widthRef.current = width;
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const bucketRef = useRef(
    desktopSplitBucket(typeof window === 'undefined' ? 1280 : window.innerWidth)
  );

  useEffect(() => {
    const onResize = () => {
      const viewport = window.innerWidth;
      const bucket = desktopSplitBucket(viewport);
      if (bucket !== bucketRef.current) {
        bucketRef.current = bucket;
        setWidth(getSavedDesktopSplitWidth(viewport));
        return;
      }
      setWidth((current) => clampDesktopSplitWidth(current, viewport));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const commit = useCallback((next: number) => {
    const viewport = window.innerWidth;
    const clamped = clampDesktopSplitWidth(next, viewport);
    setWidth(clamped);
    setSavedDesktopSplitWidth(clamped, viewport);
    return clamped;
  }, []);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { startX: event.clientX, startWidth: width };
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const next = clampDesktopSplitWidth(
      drag.startWidth + (event.clientX - drag.startX),
      window.innerWidth
    );
    widthRef.current = next;
    setWidth(next);
  };

  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
    setSavedDesktopSplitWidth(widthRef.current, window.innerWidth);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      commit(width - 16);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      commit(width + 16);
    } else if (event.key === 'Home') {
      event.preventDefault();
      commit(DESKTOP_SPLIT_MIN);
    } else if (event.key === 'End') {
      event.preventDefault();
      commit(DESKTOP_SPLIT_MAX);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      commit(DESKTOP_SPLIT_DEFAULT);
    }
  };

  const onDoubleClick = () => {
    commit(DESKTOP_SPLIT_DEFAULT);
  };

  return (
    <div className="relative hidden min-h-0 shrink-0 md:flex" style={{ width }}>
      <div
        data-desktop-list-pane=""
        className="flex min-h-0 min-w-0 flex-1 flex-col border-r border-border bg-muted/20"
      >
        {children}
      </div>
      <div
        role="separator"
        tabIndex={0}
        aria-orientation="vertical"
        aria-label={t('conversationList.resizeList')}
        aria-valuenow={width}
        aria-valuemin={DESKTOP_SPLIT_MIN}
        aria-valuemax={DESKTOP_SPLIT_MAX}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onKeyDown}
        onDoubleClick={onDoubleClick}
        className="conversation-split-handle"
      >
        <span className="conversation-split-grip" aria-hidden="true" />
      </div>
    </div>
  );
}
