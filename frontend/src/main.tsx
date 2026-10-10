import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { i18nReady } from './i18n';
import './index.css';
import './themes.css';
import './styles.css';
import { applyStartupTheme, initFollowOSListener } from './utils/theme';
import { applyFontScale, getSavedFontScale } from './utils/fontScale';
import { migrateLegacyLocalStoragePrefix } from './utils/legacyStoragePrefix';
import { initAppViewport } from './utils/appViewport';
import { clearReloadFlag, installPreloadErrorHandler, reloadOnce } from './utils/chunkReload';
import { PushSubscriptionProvider } from './contexts/PushSubscriptionContext';

migrateLegacyLocalStoragePrefix();
// Apply the cached theme before first render — unless the server already wrote
// one into the page, in which case overwriting it is what caused the flash.
applyStartupTheme();
// Re-apply when the OS color-scheme preference changes, if on "Follow OS".
initFollowOSListener();
applyFontScale(getSavedFontScale());
initAppViewport();

installPreloadErrorHandler();

function renderBootError(root: HTMLElement): void {
  // i18n is unavailable here, so this screen is deliberately plain English.
  root.innerHTML =
    '<div role="alert" style="padding:2rem;font-family:sans-serif">' +
    '<p>Meshloom could not load its language files. It may have just been updated.</p>' +
    '<button type="button" onclick="location.reload()">Reload</button></div>';
}

// Wait for the active language bundle so the first paint never shows raw keys.
i18nReady.then(
  () => {
    createRoot(document.getElementById('root')!).render(
      <StrictMode>
        <PushSubscriptionProvider>
          <App />
        </PushSubscriptionProvider>
      </StrictMode>
    );
    clearReloadFlag();
  },
  (err: unknown) => {
    console.error('Failed to load the UI language:', err);
    // A stale page after an update: fetch the fresh index once, else show a static error.
    if (!reloadOnce()) renderBootError(document.getElementById('root')!);
  }
);

// Register service worker for Web Push (requires secure context)
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('./sw.js').catch((err) => {
    console.warn('Service worker registration failed:', err);
  });
}
