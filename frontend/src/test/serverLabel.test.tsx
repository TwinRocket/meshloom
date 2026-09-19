import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ServerLabelBand } from '../components/ServerLabelBand';
import { normalizeHex } from '../components/ui/color-picker';
import type { ServerLabel } from '../types';
import { setLocalLabel } from '../utils/localLabel';
import {
  DEFAULT_SERVER_LABEL,
  normalizeServerLabel,
  serverLabelStyle,
  serverLabelToAdopt,
  serverLabelVisible,
} from '../utils/serverLabel';

function label(overrides: Partial<ServerLabel> = {}): ServerLabel {
  return { ...DEFAULT_SERVER_LABEL, ...overrides };
}

describe('server label', () => {
  afterEach(() => setLocalLabel('', '#062d60'));

  it('shows nothing until it is named', () => {
    expect(serverLabelVisible(label())).toBe(false);
    expect(serverLabelVisible(label({ text: '   ' }))).toBe(false);
    expect(serverLabelVisible(label({ text: 'Var' }))).toBe(true);
  });

  it('renders no band when unnamed, so the option costs nothing', () => {
    render(<ServerLabelBand label={label()} />);
    expect(screen.queryByTestId('server-label-band')).toBeNull();
  });

  it('renders the name when there is one', () => {
    render(<ServerLabelBand label={label({ text: 'Belle-mère' })} />);
    expect(screen.getByTestId('server-label-band').textContent).toBe('Belle-mère');
  });

  it('keeps a size the band can actually wear', () => {
    // A number arriving from an older instance, or a hand-edited one, must not
    // produce a band that swallows the screen or text nobody can read.
    expect(normalizeServerLabel({ size_px: 400 }).size_px).toBe(48);
    expect(normalizeServerLabel({ size_px: 2 }).size_px).toBe(12);
    expect(normalizeServerLabel({ size_px: Number.NaN }).size_px).toBe(14);
  });

  it('ignores a font it cannot honour', () => {
    // A family the browser lacks renders as something else, which is worse than
    // the one that was asked for being refused.
    expect(normalizeServerLabel({ font: 'comic' as never }).font).toBe('system');
  });

  it('scales the band with its text rather than fixing its height', () => {
    const small = serverLabelStyle(label({ size_px: 12 }));
    const large = serverLabelStyle(label({ size_px: 48 }));
    expect(parseInt(small.padding, 10)).toBeLessThan(parseInt(large.padding, 10));
    expect(large.fontSize).toBe('48px');
  });

  it('carries weight and slant into the style', () => {
    expect(serverLabelStyle(label({ bold: true })).fontWeight).toBe(700);
    expect(serverLabelStyle(label({ italic: true })).fontStyle).toBe('italic');
  });

  describe('taking over a label this browser was keeping', () => {
    it('hands the old one to the instance when it has none', () => {
      setLocalLabel('Maison', '#15803d');
      expect(serverLabelToAdopt(label())).toMatchObject({ text: 'Maison', color: '#15803d' });
    });

    it('leaves the instance alone once it has its own', () => {
      setLocalLabel('Maison', '#15803d');
      expect(serverLabelToAdopt(label({ text: 'Var' }))).toBeNull();
    });

    it('does nothing when neither has one', () => {
      expect(serverLabelToAdopt(label())).toBeNull();
    });
  });
});

describe('hex entry', () => {
  it('accepts what a person types and refuses what it cannot use', () => {
    expect(normalizeHex('#AABBCC')).toBe('#aabbcc');
    expect(normalizeHex('aabbcc')).toBe('#aabbcc');
    expect(normalizeHex(' #aabbcc ')).toBe('#aabbcc');
    expect(normalizeHex('#abc')).toBeNull();
    expect(normalizeHex('rouge')).toBeNull();
  });
});
