import { Component, Suspense, type ErrorInfo, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from './ui/button';

function ChunkError() {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-sm text-muted-foreground"
    >
      <p>{t('chunkError.message')}</p>
      <Button size="sm" onClick={() => window.location.reload()}>
        {t('chunkError.reload')}
      </Button>
    </div>
  );
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Lazy chunk failed to load:', error, info.componentStack);
  }
  render() {
    return this.state.failed ? <ChunkError /> : this.props.children;
  }
}

/** Suspense + error boundary: a failed lazy import shows a reload prompt, not a white screen. */
export function LazyBoundary({ fallback, children }: { fallback: ReactNode; children: ReactNode }) {
  return (
    <Boundary>
      <Suspense fallback={fallback}>{children}</Suspense>
    </Boundary>
  );
}
