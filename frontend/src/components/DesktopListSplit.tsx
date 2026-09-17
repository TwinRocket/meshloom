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

  useEffect(() => {
    const onResize = () => {
      setWidth((current) => clampDesktopSplitWidth(current, window.innerWidth));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const commit = useCallback((next: number) => {
    const clamped = clampDesktopSplitWidth(next, window.innerWidth);
    setWidth(clamped);
    setSavedDesktopSplitWidth(clamped);
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
    setSavedDesktopSplitWidth(widthRef.current);
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
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
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
