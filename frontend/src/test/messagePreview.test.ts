import { describe, it, expect } from 'vitest';
import { describeMessagePreview } from '../utils/messagePreview';
import { formatGif, formatLocation } from '../utils/meshcoreOpenPayloads';
import i18n from '../i18n';

/**
 * Rich payloads travel as ordinary plaintext, so a list that prints the body
 * prints the wire form. A conversation whose last message was a GIF read
 * `g:APqEbxBsVIkWSuFpth`.
 */

describe('describeMessagePreview', () => {
  it('names a GIF instead of showing its id', () => {
    expect(describeMessagePreview('g:APqEbxBsVIkWSuFpth')).toBe(i18n.t('messagePreview.gif'));
    expect(describeMessagePreview(formatGif('abc123'))).toBe(i18n.t('messagePreview.gif'));
  });

  it('prefers what the sender called the place over the word for it', () => {
    const pin = formatLocation(43.58, 7.12, 'Antibes', 'loc')!;
    expect(describeMessagePreview(pin)).toBe(
      i18n.t('messagePreview.locationNamed', { label: 'Antibes' })
    );
  });

  it('falls back to the plain word when a pin carries no label', () => {
    // Our own encoder substitutes "pin" for an empty label, so this case only
    // arrives from the wire — which the parser accepts.
    expect(describeMessagePreview('m:43.580000,7.120000||loc')).toBe(
      i18n.t('messagePreview.location')
    );
  });

  it('shows the emoji a reaction carries', () => {
    // The index is two hex digits, not one.
    const out = describeMessagePreview('r:1a2b:00');
    expect(out).not.toContain('r:1a2b');
    expect(out).toContain('👍');
  });

  it('leaves ordinary text exactly as it is', () => {
    // Including text that merely starts with a letter and a colon.
    for (const text of ['bonjour', 'note: rendez-vous demain', 'https://example.org', '  ']) {
      expect(describeMessagePreview(text)).toBe(text);
    }
  });

  it('does not mistake a malformed payload for a rich one', () => {
    expect(describeMessagePreview('g:')).toBe('g:');
    expect(describeMessagePreview('m:200,999|nulle part|loc')).toBe('m:200,999|nulle part|loc');
  });
});
