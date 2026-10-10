import { describe, expect, it } from 'vitest';

import { exactHashtagInput, normalizeHashtagInput } from '../utils/hashtagInput';
import { deriveHashtagKeyHex } from '../utils/hashtagKey';

describe('normalizeHashtagInput (official app rule, user input only)', () => {
  it('trims, lowercases and adds the #', () => {
    expect(normalizeHashtagInput(' Fr ')).toEqual({ ok: true, name: '#fr' });
    expect(normalizeHashtagInput('#Test-1')).toEqual({ ok: true, name: '#test-1' });
    expect(normalizeHashtagInput('mesh-room')).toEqual({ ok: true, name: '#mesh-room' });
  });

  it('rejects spaces, accents and symbols', () => {
    for (const raw of ['#a b', '#é', 'cats&dogs', 'bad_room', '##x']) {
      expect(normalizeHashtagInput(raw)).toEqual({
        ok: false,
        error: 'newMessage.hashtagInvalid',
      });
    }
  });

  it('rejects an empty name', () => {
    for (const raw of ['', '   ', '#', ' # ']) {
      expect(normalizeHashtagInput(raw)).toEqual({
        ok: false,
        error: 'newMessage.hashtagRequired',
      });
    }
  });

  it('accepts 30 bytes and rejects 31', () => {
    expect(normalizeHashtagInput(`#${'a'.repeat(29)}`)).toEqual({
      ok: true,
      name: `#${'a'.repeat(29)}`,
    });
    expect(normalizeHashtagInput('a'.repeat(30))).toEqual({
      ok: false,
      error: 'newMessage.hashtagTooLong',
    });
  });
});

describe('exactHashtagInput (extended names option)', () => {
  it('keeps case, inner spaces and symbols; drops one leading #', () => {
    expect(exactHashtagInput(' #Cats & Dogs ')).toEqual({ ok: true, name: '#Cats & Dogs' });
    expect(exactHashtagInput('##x')).toEqual({ ok: true, name: '##x' });
  });

  it('rejects empty and more than 32 bytes', () => {
    expect(exactHashtagInput('#')).toEqual({ ok: false, error: 'newMessage.hashtagRequired' });
    expect(exactHashtagInput('é'.repeat(16))).toEqual({
      ok: false,
      error: 'newMessage.hashtagTooLongExtended',
    });
  });
});

describe('key derivation stays exact', () => {
  it('distinguishes #Fr from #fr, and keeps whitespace', () => {
    expect(deriveHashtagKeyHex('#Fr')).not.toBe(deriveHashtagKeyHex('#fr'));
    expect(deriveHashtagKeyHex('fr ')).not.toBe(deriveHashtagKeyHex('fr'));
    // Normalizing the input is what makes " Fr " reach the #fr channel.
    const parsed = normalizeHashtagInput(' Fr ');
    expect(parsed.ok && deriveHashtagKeyHex(parsed.name)).toBe(deriveHashtagKeyHex('#fr'));
  });
});
