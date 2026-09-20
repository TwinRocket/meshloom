import { describe, it, expect } from 'vitest';
import { describeMessagePreview, reactionTargetExcerptFromMessages } from '../utils/messagePreview';
import { formatGif, formatLocation, formatOpenReaction } from '../utils/meshcoreOpenPayloads';
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

  it('names who reacted when the channel prefix is still on the wire', () => {
    expect(describeMessagePreview('r-06-PSCEL-Basefer: r:90fd:00')).toBe(
      i18n.t('messagePreview.theyReacted', { name: 'r-06-PSCEL-Basefer', emoji: '👍' })
    );
    expect(describeMessagePreview('r-06-PSCEL-Basefer:r:90fd:00')).toBe(
      i18n.t('messagePreview.theyReacted', { name: 'r-06-PSCEL-Basefer', emoji: '👍' })
    );
  });

  it('says you reacted when the last message is outgoing', () => {
    expect(describeMessagePreview('r:1a2b:00', { outgoing: true })).toBe(
      i18n.t('messagePreview.youReacted', { emoji: '👍' })
    );
    expect(describeMessagePreview('Radio: r:1a2b:00', { selfName: 'Radio' })).toBe(
      i18n.t('messagePreview.youReacted', { emoji: '👍' })
    );
  });

  it('quotes the target when an excerpt is known', () => {
    expect(describeMessagePreview('Alice: r:1a2b:00', { targetExcerpt: 'Encore un truc ne' })).toBe(
      i18n.t('messagePreview.theyReactedTo', {
        name: 'Alice',
        emoji: '👍',
        excerpt: 'Encore un truc ne',
      })
    );
    expect(
      describeMessagePreview('r:1a2b:00', { outgoing: true, targetExcerpt: 'hello world' })
    ).toBe(i18n.t('messagePreview.youReactedTo', { emoji: '👍', excerpt: 'hello world' }));
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

  it('resolves an Open reaction target excerpt from loaded messages', () => {
    const wire = formatOpenReaction(1700000000, 'Alice', 'Encore un truc ne va pas', '👍');
    expect(
      reactionTargetExcerptFromMessages(`Bob: ${wire}`, [
        {
          type: 'CHAN',
          sender_timestamp: 1700000000,
          sender_name: 'Alice',
          text: 'Alice: Encore un truc ne va pas',
        },
        {
          type: 'CHAN',
          sender_timestamp: 1700000002,
          sender_name: 'Bob',
          text: `Bob: ${wire}`,
        },
      ])
    ).toBe('Encore un truc ne va pas');
  });
});
