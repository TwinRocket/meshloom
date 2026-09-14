export interface Theme {
  id: string;
  name: string;
  /** 6 hex colors for the swatch preview: [bg, card, primary, accent, warm, cool] */
  swatches: [string, string, string, string, string, string];
  /** Hex background color for the PWA theme-color meta tag */
  metaThemeColor: string;
}

export const THEME_CHANGE_EVENT = 'meshloom-theme-change';

export const FOLLOW_OS_THEME_ID = 'follow-os';

export const THEMES: Theme[] = [
  {
    id: 'original',
    name: 'Meshloom',
    swatches: ['#0A0C10', '#11141A', '#BF5AF2', '#1A1D28', '#22D3EE', '#7C3AED'],
    metaThemeColor: '#0A0C10',
  },
  {
    id: 'light',
    name: 'Light',
    swatches: ['#F4F6FA', '#FFFFFF', '#7A2BB8', '#E8EAF2', '#0891B2', '#7C3AED'],
    metaThemeColor: '#F4F6FA',
  },
  {
    id: FOLLOW_OS_THEME_ID,
    name: 'OS Light/Dark Mode',
    // Top row: light theme preview colors; bottom row: original (dark) preview colors
    swatches: ['#F4F6FA', '#FFFFFF', '#7A2BB8', '#0A0C10', '#11141A', '#BF5AF2'],
    metaThemeColor: '#0A0C10',
  },
  {
    id: 'ios',
    name: 'iPhone',
    swatches: ['#F2F2F7', '#FFFFFF', '#007AFF', '#E5E5EA', '#FF9F0A', '#34C759'],
    metaThemeColor: '#F2F2F7',
  },
  {
    id: 'paper-grove',
    name: 'Paper Grove',
    swatches: ['#F7F1E4', '#FFF9EE', '#2F9E74', '#E7DEC8', '#E76F51', '#5C7CFA'],
    metaThemeColor: '#F7F1E4',
  },
  {
    id: 'cyberpunk',
    name: 'Cyberpunk',
    swatches: ['#07080A', '#0D1112', '#00FF41', '#141E17', '#FAFF00', '#FF2E6C'],
    metaThemeColor: '#07080A',
  },
  {
    id: 'obsidian-glass',
    name: 'Obsidian Glass',
    swatches: ['#0C0E12', '#151821', '#D4A070', '#1E2230', '#D4924A', '#5B82B4'],
    metaThemeColor: '#0C0E12',
  },
  {
    id: 'solar-flare',
    name: 'Solar Flare',
    swatches: ['#0D0607', '#151012', '#FF0066', '#2D1D22', '#FF8C1A', '#30ACD4'],
    metaThemeColor: '#0D0607',
  },
  {
    id: 'lagoon-pop',
    name: 'Lagoon Pop',
    swatches: ['#081A22', '#0F2630', '#23D7C6', '#173844', '#FF7A66', '#7C83FF'],
    metaThemeColor: '#081A22',
  },
  {
    id: 'candy-dusk',
    name: 'Candy Dusk',
    swatches: ['#140F24', '#201736', '#FF79C9', '#2A2144', '#FFC857', '#8BE9FD'],
    metaThemeColor: '#140F24',
  },
  {
    id: 'high-contrast',
    name: 'High Contrast',
    swatches: ['#000000', '#141414', '#3B9EFF', '#1E1E1E', '#FFB800', '#FF4757'],
    metaThemeColor: '#000000',
  },
  {
    id: 'monochrome',
    name: 'Monochrome',
    swatches: ['#FAFAFA', '#FFFFFF', '#111111', '#EAEAEA', '#8A8A8A', '#4A4A4A'],
    metaThemeColor: '#FAFAFA',
  },
  {
    id: 'windows-95',
    name: 'Windows 95',
    swatches: ['#008080', '#C0C0C0', '#000080', '#DFDFDF', '#FFDE59', '#000000'],
    metaThemeColor: '#008080',
  },
];

const THEME_KEY = 'meshloom-theme';

/**
 * Whether the server wrote the theme into the page it served.
 *
 * It does that whenever the backend serves the shell, which is the deployed case:
 * the page then paints in the right theme on its very first frame. It is absent
 * when the files are served by something else — the dev server, or a static host
 * in front of `dist/` — and there the cached choice below takes over, so the flash
 * happens once on a device rather than on every load.
 */
/**
 * Remember a theme locally without applying it.
 *
 * The local copy is a cache, not the authority: it is what the next load paints
 * from before anything is fetched, which is what makes the flash happen once on a
 * device rather than on every visit.
 */
export function cacheTheme(themeId: string): void {
  try {
    localStorage.setItem(THEME_KEY, themeId);
  } catch {
    // localStorage may be unavailable; the stored value will say so again.
  }
}

export function serverChoseTheme(): boolean {
  return (
    typeof document !== 'undefined' && document.documentElement.hasAttribute('data-theme-server')
  );
}

/**
 * Apply the theme for the first paint, without undoing the server's work.
 *
 * `applyTheme` writes the attribute unconditionally, so calling it at startup
 * overwrote an injected theme with the local default and reintroduced the flash
 * it was there to remove.
 */
export function applyStartupTheme(): void {
  if (serverChoseTheme()) return;
  applyTheme(getSavedTheme());
}

export function getSavedTheme(): string {
  try {
    return localStorage.getItem(THEME_KEY) ?? 'original';
  } catch {
    return 'original';
  }
}

/** Resolves "Follow OS" to a concrete theme id by inspecting the OS color-scheme preference. */
function resolveFollowOS(): 'original' | 'light' {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'original';
  }
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'original';
}

/**
 * Returns the concrete theme id currently applied to the document.
 * Unlike getSavedTheme, this resolves 'follow-os' to 'original' or 'light'.
 */
export function getEffectiveTheme(): string {
  const saved = getSavedTheme();
  return saved === FOLLOW_OS_THEME_ID ? resolveFollowOS() : saved;
}

export function applyTheme(themeId: string): void {
  try {
    localStorage.setItem(THEME_KEY, themeId);
  } catch {
    // localStorage may be unavailable
  }

  const effective = themeId === FOLLOW_OS_THEME_ID ? resolveFollowOS() : themeId;

  if (effective === 'original') {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = effective;
  }

  // Update PWA theme-color meta tag — reflect the effective (rendered) theme.
  const theme = THEMES.find((t) => t.id === effective);
  if (theme) {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', theme.metaThemeColor);
    }
  }

  if (typeof window !== 'undefined') {
    // Detail is the saved theme id (including 'follow-os'); listeners that need
    // the rendered appearance should call getEffectiveTheme().
    window.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT, { detail: themeId }));
  }
}

let followOSInitialized = false;

/**
 * Installs a one-time listener on prefers-color-scheme so that when the user is
 * on "Follow OS", OS appearance changes re-apply the theme. Safe to call once
 * from app bootstrap.
 */
export function initFollowOSListener(): void {
  if (followOSInitialized) return;
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
  followOSInitialized = true;
  const mql = window.matchMedia('(prefers-color-scheme: light)');
  const handler = () => {
    if (getSavedTheme() === FOLLOW_OS_THEME_ID) {
      applyTheme(FOLLOW_OS_THEME_ID);
    }
  };
  if (typeof mql.addEventListener === 'function') {
    mql.addEventListener('change', handler);
  } else if (typeof (mql as MediaQueryList).addListener === 'function') {
    // Safari < 14 fallback
    (mql as MediaQueryList).addListener(handler);
  }
}
