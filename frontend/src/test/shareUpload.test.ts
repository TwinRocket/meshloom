/**
 * `api.uploadShareFile` headers (MSHL-378): an encrypted upload names its
 * conversation by an opaque id, never by a channel key.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';

class FakeXhr {
  static last: FakeXhr | null = null;
  headers: Record<string, string> = {};
  method = '';
  url = '';
  status = 0;
  responseText = '';
  responseURL = '';
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  body: unknown = null;

  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  getResponseHeader() {
    return null;
  }
  abort() {}
  send(body: unknown) {
    this.body = body;
    this.status = 201;
    this.responseText = JSON.stringify({
      id: 'AbCdEfG',
      url: 'https://meshl.ink/AbCdEfG',
      expires_at: 1,
      size: 4,
      mode: this.headers['X-Share-Mode'],
      text: 'x',
    });
    queueMicrotask(() => this.onload?.());
  }
}

const CHANNEL_KEY = '5F1CA2B7D93E4086A1C2E3F405162738';
const CONVERSATION_ID = '0123456789abcdef01234567';

describe('uploadShareFile', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeXhr.last = null;
  });

  it('uploads in public mode without any conversation header', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    const file = await api.uploadShareFile(new Blob(['abcd'], { type: 'image/png' }));
    expect(file.mode).toBe('public');
    const headers = FakeXhr.last!.headers;
    expect(headers['X-Share-Mode']).toBe('public');
    expect(headers['Content-Type']).toBe('image/png');
    expect(Object.keys(headers).filter((h) => h.startsWith('X-Share-Conversation'))).toEqual([]);
  });

  it('names a channel by its conversation id and never sends a key', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    const file = await api.uploadShareFile(new Blob(['abcd'], { type: 'image/png' }), {
      encrypted: { type: 'channel', id: CONVERSATION_ID },
    });
    expect(file.mode).toBe('encrypted');
    const headers = FakeXhr.last!.headers;
    expect(headers['X-Share-Mode']).toBe('encrypted');
    expect(headers['X-Share-Conversation-Type']).toBe('channel');
    expect(headers['X-Share-Conversation-Id']).toBe(CONVERSATION_ID);
    expect(headers).not.toHaveProperty('X-Share-Conversation-Key');
    const wire = JSON.stringify(headers).toLowerCase();
    expect(wire).not.toContain(CHANNEL_KEY.toLowerCase());
  });

  describe('X-Share-Name', () => {
    const blob = () => new Blob(['abcd'], { type: 'image/png' });
    const conversation = { type: 'channel' as const, id: CONVERSATION_ID };

    it('percent-encodes the name so the header stays ASCII and the backend unquotes it', async () => {
      vi.stubGlobal('XMLHttpRequest', FakeXhr);
      const name = 'été à la plage #1 100% 日本語.jpg';
      await api.uploadShareFile(blob(), { encrypted: conversation, name });
      const value = FakeXhr.last!.headers['X-Share-Name'];
      expect(value).toBe(encodeURIComponent(name));
      expect(value).toMatch(/^[\x20-\x7e]+$/);
      expect(value).not.toMatch(/[ #]/);
      // What the backend does with urllib.parse.unquote(raw, encoding='utf-8').
      expect(decodeURIComponent(value)).toBe(name);
    });

    it('keeps a plain name readable and leaves the header out without a name', async () => {
      vi.stubGlobal('XMLHttpRequest', FakeXhr);
      await api.uploadShareFile(blob(), { encrypted: conversation, name: 'summit.jpg' });
      expect(FakeXhr.last!.headers['X-Share-Name']).toBe('summit.jpg');
      await api.uploadShareFile(blob(), { encrypted: conversation });
      expect(FakeXhr.last!.headers).not.toHaveProperty('X-Share-Name');
    });

    it('never sends a name in public mode', async () => {
      vi.stubGlobal('XMLHttpRequest', FakeXhr);
      await api.uploadShareFile(blob(), { name: 'summit.jpg' });
      expect(FakeXhr.last!.headers).not.toHaveProperty('X-Share-Name');
    });
  });
});
