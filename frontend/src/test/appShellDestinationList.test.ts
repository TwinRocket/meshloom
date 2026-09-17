import { describe, expect, it } from 'vitest';

import { shouldShowDestinationList } from '../components/AppShell';

describe('shouldShowDestinationList', () => {
  it('keeps the conversation list beside chats, rooms, and repeaters', () => {
    expect(shouldShowDestinationList('conversations')).toBe(true);
  });

  it('keeps the settings index beside a settings section', () => {
    expect(shouldShowDestinationList('settings')).toBe(true);
  });

  it('does not restore the list on a tool or the map', () => {
    expect(shouldShowDestinationList('tools')).toBe(false);
    expect(shouldShowDestinationList('map')).toBe(false);
  });

  it('hides the list when the reader folded it', () => {
    expect(shouldShowDestinationList('conversations', true)).toBe(false);
    expect(shouldShowDestinationList('settings', true)).toBe(false);
  });
});
