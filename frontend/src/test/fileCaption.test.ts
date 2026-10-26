import { describe, expect, it } from 'vitest';

import { estimatedFileMessageBytes, fileMessage, fitsLimit } from '../utils/fileCaption';

describe('fileCaption', () => {
  it('joins the suggested text and a trimmed caption, or keeps the text alone', () => {
    expect(fileMessage('\u{1F4F7} https://meshl.ink/AbCdEfG', '  hello ')).toBe(
      '\u{1F4F7} https://meshl.ink/AbCdEfG hello'
    );
    expect(fileMessage('\u{1F4F7} https://meshl.ink/AbCdEfG', '   ')).toBe(
      '\u{1F4F7} https://meshl.ink/AbCdEfG'
    );
  });
  it('estimates the 30 bytes of the link alone on meshl.ink, plus the caption', () => {
    expect(estimatedFileMessageBytes('meshl.ink', '')).toBe(30);
    expect(estimatedFileMessageBytes('meshl.ink', 'abc')).toBe(34);
    expect(estimatedFileMessageBytes('meshl.ink', 'é')).toBe(33);
  });
  it('judges the limit strictly, like the composer', () => {
    expect(fitsLimit(155, 156)).toBe(true);
    expect(fitsLimit(156, 156)).toBe(false);
    expect(fitsLimit(10_000, null)).toBe(true);
  });
});
