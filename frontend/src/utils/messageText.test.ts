import { describe, expect, it } from 'vitest';

import { hasUnexpectedControlChars } from './messageText';

describe('hasUnexpectedControlChars', () => {
  it.each([
    ['C0 control', '\x0e'],
    ['DEL', '\x7f'],
    ['zero-width space', '​'],
    ['right-to-left mark', '‏'],
    ['bidi override (RLO)', '‮'],
    ['bidi embedding (LRE)', '‪'],
    ['bidi isolate (FSI)', '⁨'],
    ['pop directional isolate', '⁩'],
  ])('flags %s', (_name, char) => {
    expect(hasUnexpectedControlChars(`ab${char}cd`)).toBe(true);
  });

  it('does not flag tab, newline, accents or CJK', () => {
    expect(hasUnexpectedControlChars('Café été\tligne\nsuite\r')).toBe(false);
    expect(hasUnexpectedControlChars('你好，世界 こんにちは 안녕하세요')).toBe(false);
  });

  it('does not flag characters just outside the ranges', () => {
    expect(hasUnexpectedControlChars('‐  ⁥⁪')).toBe(false);
  });
});
