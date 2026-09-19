import type { ServerLabel } from '../types';

import { getLocalLabel } from './localLabel';

export const DEFAULT_SERVER_LABEL: ServerLabel = {
  text: '',
  color: '#062d60',
  size_px: 14,
  bold: false,
  italic: false,
  font: 'system',
};

/** Families every browser has. A named font nobody installed renders as another one. */
const FONT_STACKS: Record<ServerLabel['font'], string> = {
  system: 'inherit',
  serif: 'Georgia, "Times New Roman", serif',
  mono: 'ui-monospace, SFMono-Regular, Menlo, monospace',
};

/**
 * Ready-made backgrounds, picked to stay apart from one another.
 *
 * Choosing a colour per server is a comparison, not a design exercise: what
 * matters is that two instances never look alike at a glance. Eight distinct
 * hues answer that in one click, where a colour wheel asks someone to invent an
 * answer and often lands on two blues nobody can tell apart.
 */
export const LABEL_COLORS = [
  '#062d60',
  '#0f766e',
  '#15803d',
  '#a16207',
  '#c2410c',
  '#b91c1c',
  '#6d28d9',
  '#334155',
] as const;

/** Offered in the picker, in pixels. The steps a mail composer offers. */
export const LABEL_SIZES = [12, 13, 14, 16, 18, 20, 24, 28, 32, 40, 48] as const;

const MIN_SIZE = 12;
const MAX_SIZE = 48;

export function normalizeServerLabel(label: Partial<ServerLabel> | null | undefined): ServerLabel {
  if (!label) return DEFAULT_SERVER_LABEL;
  return {
    text: typeof label.text === 'string' ? label.text : '',
    color: typeof label.color === 'string' ? label.color : DEFAULT_SERVER_LABEL.color,
    size_px:
      typeof label.size_px === 'number' && Number.isFinite(label.size_px)
        ? Math.min(MAX_SIZE, Math.max(MIN_SIZE, Math.round(label.size_px)))
        : DEFAULT_SERVER_LABEL.size_px,
    bold: label.bold === true,
    italic: label.italic === true,
    font: label.font && label.font in FONT_STACKS ? label.font : 'system',
  };
}

export function serverLabelVisible(label: ServerLabel): boolean {
  return label.text.trim().length > 0;
}

/** The style the band and its preview both use, so they cannot drift apart. */
export function serverLabelStyle(label: ServerLabel): {
  fontSize: string;
  padding: string;
  fontWeight: number;
  fontStyle: string;
  fontFamily: string;
} {
  // Padding tracks the text so the band stays proportionate at every size
  // instead of a tall strip around small text, or cramped large text.
  const padding = Math.round(label.size_px * 0.35);
  return {
    fontSize: `${label.size_px}px`,
    padding: `${padding}px 1rem`,
    fontWeight: label.bold ? 700 : 500,
    fontStyle: label.italic ? 'italic' : 'normal',
    fontFamily: FONT_STACKS[label.font],
  };
}

/**
 * The label a browser was carrying before this moved to the instance.
 *
 * Returned only when the instance has none, so the first device to open a
 * Meshloom that already had a label hands it over instead of losing it.
 */
export function serverLabelToAdopt(instance: ServerLabel): ServerLabel | null {
  if (serverLabelVisible(instance)) return null;
  const browser = getLocalLabel();
  if (!browser.text.trim()) return null;
  return { ...DEFAULT_SERVER_LABEL, text: browser.text, color: browser.color };
}
