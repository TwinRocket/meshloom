import { describe, expect, it } from 'vitest';

import { REACTION_EMOJIS } from '../utils/meshcoreOpenPayloads';
import { filterEmojis } from '../utils/emojiSearch';

describe('filterEmojis', () => {
  it('returns unique emojis when the query is empty', () => {
    const filtered = filterEmojis(REACTION_EMOJIS, '');
    expect(filtered.length).toBeLessThan(REACTION_EMOJIS.length);
    expect(filtered).toContain('👍');
    expect(filtered.filter((emoji) => emoji === '👍')).toHaveLength(1);
  });

  it('matches English and accented French keywords', () => {
    expect(filterEmojis(REACTION_EMOJIS, 'fire')).toContain('🔥');
    expect(filterEmojis(REACTION_EMOJIS, 'feu')).toContain('🔥');
    expect(filterEmojis(REACTION_EMOJIS, 'coeur')).toContain('❤️');
    expect(filterEmojis(REACTION_EMOJIS, 'cœur')).toContain('❤️');
  });

  it('returns nothing for an unknown term', () => {
    expect(filterEmojis(REACTION_EMOJIS, 'xyzzy')).toEqual([]);
  });
});
