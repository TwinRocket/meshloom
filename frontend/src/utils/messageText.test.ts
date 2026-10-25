import { describe, expect, it } from 'vitest';

import { hasUnexpectedControlChars, isolateBidi } from './messageText';

describe('hasUnexpectedControlChars', () => {
  it.each([
    ['C0 control', '\u000e'],
    ['DEL', '\u007f'],
    ['zero-width space', '\u200b'],
    ['word joiner', '\u2060'],
    ['BOM / zero-width no-break space', '\ufeff'],
    ['Arabic letter mark', '\u061c'],
    ['bidi override (RLO)', '\u202e'],
    ['bidi override (LRO)', '\u202d'],
    ['bidi embedding (LRE)', '\u202a'],
    ['bidi isolate (FSI)', '\u2068'],
    ['pop directional isolate', '\u2069'],
  ])('flags %s', (_name, char) => {
    expect(hasUnexpectedControlChars(`ab${char}cd`)).toBe(true);
  });

  it('does not flag tab, newline, accents or CJK', () => {
    expect(hasUnexpectedControlChars('Caf\u00e9 \u00e9t\u00e9\tligne\nsuite\r')).toBe(false);
    expect(hasUnexpectedControlChars('\u4f60\u597d')).toBe(false);
  });

  it('does not flag an emoji ZWJ sequence', () => {
    expect(hasUnexpectedControlChars('\u{1F468}\u200d\u{1F469}\u200d\u{1F467}')).toBe(false);
    expect(hasUnexpectedControlChars('\u{1F3F3}\ufe0f\u200d\u{1F308}')).toBe(false);
  });

  it('does not flag a Persian word with ZWNJ', () => {
    expect(hasUnexpectedControlChars('\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645')).toBe(
      false
    );
  });

  it('does not flag LRM/RLM in Arabic or Hebrew names', () => {
    expect(hasUnexpectedControlChars('\u05d3\u05e0\u05d9\u200f (\u05d0)\u200e')).toBe(false);
    expect(hasUnexpectedControlChars('\u0645\u062d\u0645\u062f\u200f')).toBe(false);
  });

  it('does not flag characters just outside the ranges', () => {
    expect(hasUnexpectedControlChars('\u2010\u2029\u202f\u205f\u2065\u206a')).toBe(false);
  });
});

describe('isolateBidi', () => {
  it('wraps text in FSI..PDI', () => {
    expect(isolateBidi('Bob')).toBe('\u2068Bob\u2069');
  });
});
