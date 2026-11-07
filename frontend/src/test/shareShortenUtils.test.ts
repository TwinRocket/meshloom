import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  AUTO_SHORTEN_MIN_BYTES,
  findLongUrls,
  isOpenPayload,
  isShareLink,
  isShortenable,
  rewriteUrls,
  shortFormBytes,
  shortenedByteLength,
} from '../utils/share';

const LONG = 'https://example.org/some/very/long/path/to/an/article?id=123456789&ref=newsletter';

describe('share utils', () => {
  it('measures a short link as scheme, host, slash and a 7-character id', () => {
    expect(shortFormBytes('meshl.ink')).toBe(25);
  });

  it('applies the auto threshold on top of the short form', () => {
    const at = `https://a.io/${'a'.repeat(13)}`;
    const over = `${at}a`;
    expect(at).toHaveLength(AUTO_SHORTEN_MIN_BYTES);
    expect(findLongUrls(`${at} ${over}`, undefined, AUTO_SHORTEN_MIN_BYTES)).toEqual([over]);
    expect(findLongUrls(`${at} ${over}`)).toEqual([at, over]);
  });

  it('counts the bytes of the text once its URLs are short links', () => {
    expect(shortenedByteLength(`a ${LONG} b ${LONG}`, [LONG])).toBe(2 + 25 + 3 + 25);
    expect(shortenedByteLength(`é ${LONG}`, [])).toBe(3 + LONG.length);
  });

  it('finds long URLs once, in order, and skips short ones', () => {
    const text = `a ${LONG} b ${LONG} https://a.io/x`;
    expect(findLongUrls(text)).toEqual([LONG]);
  });

  it('never selects a service link, on any path of a share host', () => {
    expect(isShareLink('https://meshl.ink/AbCdEfG')).toBe(true);
    expect(isShareLink('https://MESHL.INK./terms')).toBe(true);
    expect(isShareLink('https://evil.example/AbCdEfG')).toBe(false);
    expect(
      findLongUrls('https://meshl.ink/terms/with/a/very/long/path/that/is/over/the/limit')
    ).toEqual([]);
    expect(findLongUrls(`https://old.example/${'x'.repeat(40)}`, ['old.example'])).toEqual([]);
  });

  it('leaves g:, m: and r: payloads alone, also behind a reply prefix', () => {
    expect(isOpenPayload('g:abc')).toBe(true);
    expect(isOpenPayload('@[Ann] m:1,2|https://x.org|loc|poi')).toBe(true);
    expect(isOpenPayload('hello g:abc')).toBe(false);
    expect(findLongUrls(`m:1,2|${LONG}|loc|poi`)).toEqual([]);
    expect(rewriteUrls(`r:ab12:3 ${LONG}`, new Map([[LONG, 'https://meshl.ink/AbCdEfG']]))).toBe(
      `r:ab12:3 ${LONG}`
    );
  });

  it('does not take trailing punctuation as part of the URL', () => {
    expect(findLongUrls(`see ${LONG}.`)).toEqual([LONG]);
    expect(findLongUrls(`(${LONG})`)).toEqual([LONG]);
  });

  it('rewrites only mapped URLs and keeps the surrounding text and prefix', () => {
    const short = 'https://meshl.ink/AbCdEfG';
    const other = 'https://other.org/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const out = rewriteUrls(`@[Ann] go ${LONG}. and ${other}!`, new Map([[LONG, short]]));
    expect(out).toBe(`@[Ann] go ${short}. and ${other}!`);
  });

  describe('isShortenable mirrors the backend URL rules', () => {
    const { cases } = JSON.parse(
      readFileSync(resolve(process.cwd(), '../tests/fixtures/share_url_cases.json'), 'utf8')
    ) as { cases: { url: string; ok: boolean }[] };

    it.each(cases)('$url -> $ok', ({ url, ok }) => {
      expect(isShortenable(url)).toBe(ok);
    });

    it('skips unshortenable URLs and keeps the public one', () => {
      const lan = 'http://192.168.1.10:8123/dashboard/overview/with/a/long/path/segment';
      expect(findLongUrls(`${lan} ${LONG}`)).toEqual([LONG]);
      expect(findLongUrls(lan)).toEqual([]);
    });
  });
});
