import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './i18n';
import './index.css';
import './themes.css';
import './styles.css';
import { getSavedTheme, applyTheme, initFollowOSListener } from './utils/theme';
import { applyFontScale, getSavedFontScale } from './utils/fontScale';
import { migrateLegacyLocalStoragePrefix } from './utils/legacyStoragePrefix';
import { initAppViewport } from './utils/appViewport';
import { PushSubscriptionProvider } from './contexts/PushSubscriptionContext';

migrateLegacyLocalStoragePrefix();
// Apply saved theme before first render
applyTheme(getSavedTheme());
// Re-apply when the OS color-scheme preference changes, if on "Follow OS".
initFollowOSListener();
applyFontScale(getSavedFontScale());
initAppViewport();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PushSubscriptionProvider>
      <App />
    </PushSubscriptionProvider>
  </StrictMode>
);

// Register service worker for Web Push (requires secure context)
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('./sw.js').catch((err) => {
    console.warn('Service worker registration failed:', err);
  });
}
