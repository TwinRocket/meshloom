import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  conversationChannelEnabled,
  type ConversationChannelInput,
} from '../utils/notificationPolicy';

// The file the backend suite reads too (`tests/test_notification_policy.py`).
interface PolicyCase {
  name: string;
  expected: boolean;
  state_key: string;
  message_type: string;
  channel_id: string;
  rules: ConversationChannelInput['rules'];
  is_hashtag: boolean;
  is_public: boolean;
  muted: boolean;
  blocked: boolean;
  now: number | null;
  mention?: boolean;
  cloud?: boolean;
}

const FIXTURE = JSON.parse(
  readFileSync(resolve(process.cwd(), '../tests/fixtures/notification_policy_cases.json'), 'utf8')
) as { cases: PolicyCase[] };

describe('conversationChannelEnabled against the shared fixture', () => {
  it('reads cases', () => {
    expect(FIXTURE.cases.length).toBeGreaterThan(30);
    expect(new Set(FIXTURE.cases.map((c) => c.name)).size).toBe(FIXTURE.cases.length);
  });

  it('covers the mention, private-channel, mute and pause cases', () => {
    const names = FIXTURE.cases.map((c) => c.name).join(' ');
    for (const word of ['mention', 'private', 'mute', 'pause', 'exception']) {
      expect(names).toContain(word);
    }
  });

  it.each(FIXTURE.cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    expect(
      conversationChannelEnabled({
        stateKey: c.state_key,
        messageType: c.message_type,
        rules: c.rules,
        channelId: c.channel_id,
        isHashtag: c.is_hashtag,
        isPublic: c.is_public,
        muted: c.muted,
        blocked: c.blocked,
        now: c.now,
        mention: c.mention ?? false,
        cloud: c.cloud ?? false,
      })
    ).toBe(c.expected);
  });
});
