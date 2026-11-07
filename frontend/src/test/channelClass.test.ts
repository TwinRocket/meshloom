import { describe, expect, it } from 'vitest';

import { channelClass, channelShareTarget, contactShareTarget } from '../utils/channelClass';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';
import { getSavedShareMode, SHARE_MODE_STORAGE, shareFileName } from '../utils/shareMode';

const KEY = '0123456789ABCDEF0123456789ABCDEF';

describe('channelClass', () => {
  it('classes Public, derived hashtag, private and unknown channels', () => {
    expect(channelClass({ key: PUBLIC_CHANNEL_KEY, key_derived: false })).toBe('public');
    expect(channelClass({ key: KEY, key_derived: true })).toBe('hashtag_derived');
    expect(channelClass({ key: KEY, key_derived: false })).toBe('private');
    // An older backend that does not say is never offered encryption.
    expect(channelClass({ key: KEY })).toBe('hashtag_derived');
  });

  it('offers encryption only for a private channel, with its sharing id', () => {
    expect(
      channelShareTarget({ key: KEY, key_derived: false, share_conversation_id: 'c1' })
    ).toEqual({ class: 'private', encryptable: true, conversation: { type: 'channel', id: 'c1' } });
    expect(channelShareTarget({ key: KEY, key_derived: false }).conversation).toBeNull();
    expect(
      channelShareTarget({ key: KEY, key_derived: true, share_conversation_id: 'c2' })
    ).toEqual({ class: 'hashtag_derived', encryptable: false, conversation: null });
    expect(channelShareTarget({ key: PUBLIC_CHANNEL_KEY, key_derived: false }).encryptable).toBe(
      false
    );
  });

  it('offers encryption in a DM, never to a room server', () => {
    expect(contactShareTarget('ab'.repeat(32), false)).toEqual({
      class: 'dm',
      encryptable: true,
      conversation: { type: 'contact', id: 'ab'.repeat(32) },
    });
    expect(contactShareTarget('ab'.repeat(32), true)).toEqual({
      class: 'room',
      encryptable: false,
      conversation: null,
    });
  });
});

describe('shareMode', () => {
  it('defaults to encrypted and reads the stored choice', () => {
    localStorage.removeItem(SHARE_MODE_STORAGE);
    expect(getSavedShareMode()).toBe('encrypted');
    localStorage.setItem(SHARE_MODE_STORAGE, 'public');
    expect(getSavedShareMode()).toBe('public');
    localStorage.setItem(SHARE_MODE_STORAGE, 'junk');
    expect(getSavedShareMode()).toBe('encrypted');
    localStorage.removeItem(SHARE_MODE_STORAGE);
  });

  it('cleans the file name the envelope carries', () => {
    expect(shareFileName('photo.jpg')).toBe('photo.jpg');
    expect(shareFileName(' a/b\\c\u0007.png ')).toBe('abc.png');
    expect(shareFileName('..')).toBeUndefined();
    expect(shareFileName('')).toBeUndefined();
    expect(shareFileName(undefined)).toBeUndefined();
    expect(Array.from(shareFileName('é'.repeat(300))!)).toHaveLength(255);
  });
});
