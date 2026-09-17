import { describe, it, expect } from 'vitest';
import { getContactAvatar, splitAvatarMonogram } from '../utils/contactAvatar';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from '../types';

describe('getContactAvatar', () => {
  it('returns complete avatar info', () => {
    const avatar = getContactAvatar('John Doe', 'abc123def456');
    expect(avatar.text).toBe('John');
    expect(avatar.background).toMatch(/^hsl\(/);
    expect(['#ffffff', '#000000']).toContain(avatar.textColor);
  });

  it('handles null name', () => {
    const avatar = getContactAvatar(null, 'abc123def456');
    expect(avatar.text).toBe('ABC1');
  });

  it('returns repeater avatar for type=2', () => {
    const avatar = getContactAvatar('Some Repeater', 'abc123def456', CONTACT_TYPE_REPEATER);
    expect(avatar.text).toBe('🛜');
    expect(avatar.background).toBe('#444444');
    expect(avatar.textColor).toBe('#ffffff');
  });

  it('repeater avatar ignores name', () => {
    const avatar1 = getContactAvatar('🚀 Rocket', 'abc123', CONTACT_TYPE_REPEATER);
    const avatar2 = getContactAvatar(null, 'xyz789', CONTACT_TYPE_REPEATER);
    expect(avatar1.text).toBe('🛜');
    expect(avatar2.text).toBe('🛜');
    expect(avatar1.background).toBe(avatar2.background);
  });

  it('returns room avatar for type=3', () => {
    const avatar = getContactAvatar('Ops Board', 'abc123def456', CONTACT_TYPE_ROOM);
    expect(avatar.text).toBe('🛖');
    expect(avatar.background).toBe('#6b4f2a');
    expect(avatar.textColor).toBe('#ffffff');
  });

  it('non-repeater types use a four-letter monogram', () => {
    const avatar0 = getContactAvatar('John', 'abc123', 0);
    const avatar1 = getContactAvatar('John', 'abc123', 1);
    expect(avatar0.text).toBe('John');
    expect(avatar1.text).toBe('John');
  });

  it('extracts emoji from name', () => {
    const avatar = getContactAvatar('John 🚀 Doe', 'abc123');
    expect(avatar.text).toBe('🚀');
  });

  it('extracts flag emoji', () => {
    const avatar = getContactAvatar('Jason 🇺🇸', 'abc123');
    expect(avatar.text).toBe('🇺🇸');
  });

  it('uses the first word of a two-word name', () => {
    const avatar = getContactAvatar('Jane Smith', 'abc123');
    expect(avatar.text).toBe('Jane');
  });

  it('takes four letters from a longer single word', () => {
    expect(getContactAvatar('Alice', 'abc123').text).toBe('Alic');
    expect(getContactAvatar('Public', 'abc123').text).toBe('Publ');
    expect(getContactAvatar('Paca', 'abc123').text).toBe('Paca');
  });

  it('keeps a three-letter word whole', () => {
    expect(getContactAvatar('Pub', 'abc123').text).toBe('Pub');
  });

  it('borrows from the next word when the first is short', () => {
    expect(getContactAvatar('Jo Doe', 'abc123').text).toBe('JoDo');
    expect(getContactAvatar('J Smith', 'abc123').text).toBe('JSmi');
    expect(getContactAvatar('Al', 'abc123').text).toBe('Al');
  });

  it('ignores a leading hashtag when reading the monogram', () => {
    expect(getContactAvatar('#meshloom', 'abc123').text).toBe('Mesh');
    expect(getContactAvatar('#public', 'abc123').text).toBe('Publ');
    expect(getContactAvatar('# Jane Doe', 'abc123').text).toBe('Jane');
  });

  it('falls back to the key when the name is only hashes', () => {
    expect(getContactAvatar('###', 'xyz789').text).toBe('XYZ7');
  });

  it('falls back to pubkey prefix for names with no letters', () => {
    const avatar = getContactAvatar('123 456', 'xyz789');
    expect(avatar.text).toBe('XYZ7');
  });

  it('returns consistent colors for same public key', () => {
    const avatar1 = getContactAvatar('A', 'abc123def456');
    const avatar2 = getContactAvatar('B', 'abc123def456');
    expect(avatar1.background).toBe(avatar2.background);
  });

  it('returns different colors for different public keys', () => {
    const avatar1 = getContactAvatar('A', 'abc123def456');
    const avatar2 = getContactAvatar('A', 'xyz789uvw012');
    expect(avatar1.background).not.toBe(avatar2.background);
  });
});

describe('splitAvatarMonogram', () => {
  it('keeps the first letter and the rest separate', () => {
    expect(splitAvatarMonogram('Publ')).toEqual({ lead: 'P', rest: 'ubl' });
    expect(splitAvatarMonogram('Paca')).toEqual({ lead: 'P', rest: 'aca' });
    expect(splitAvatarMonogram('🚀')).toEqual({ lead: '🚀', rest: '' });
  });
});
