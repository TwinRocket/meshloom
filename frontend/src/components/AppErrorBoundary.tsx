import { Component, type ErrorInfo, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { clearLastViewedConversation } from '../utils/lastViewedConversation';
import { Button } from './ui/button';

function AppError({ compact }: { compact: boolean }) {
  const { t } = useTranslation();
  const goHome = () => {
    // Forget the conversation that may have caused the failure, so a reload does not repeat it.
    clearLastViewedConversation();
    try {
      window.location.hash = '';
    } catch {
      // ignore
    }
    window.location.reload();
  };
  return (
    <div
      role="alert"
      data-testid="app-error-boundary"
      className={
        compact
          ? 'flex flex-1 flex-col items-center justify-center gap-3 p-4 text-sm text-muted-foreground'
          : 'flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-sm text-foreground'
      }
    >
      <p>{t('appError.message')}</p>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => window.location.reload()}>
          {t('appError.reload')}
        </Button>
        <Button size="sm" variant="outline" onClick={goHome}>
          {t('appError.home')}
        </Button>
      </div>
    </div>
  );
}

interface Props {
  children: ReactNode;
  /** Smaller layout for a boundary around one pane rather than the whole app. */
  compact?: boolean;
}

/** Catches any render error below it, so one bad message cannot blank the whole app. */
export class AppErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Render error caught by boundary:', error, info.componentStack);
  }
  render() {
    return this.state.failed ? (
      <AppError compact={this.props.compact ?? false} />
    ) : (
      this.props.children
    );
  }
}
