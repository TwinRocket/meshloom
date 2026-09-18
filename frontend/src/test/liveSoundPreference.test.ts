import { describe, expect, it } from 'vitest';

import { isLiveSoundTheme } from '../utils/liveSoundPreference';

describe('liveSoundPreference', () => {
  it('accepts only known theme tokens', () => {
    expect(isLiveSoundTheme('bubbles')).toBe(true);
    expect(isLiveSoundTheme('off')).toBe(true);
    expect(isLiveSoundTheme('pew')).toBe(false);
  });
});
