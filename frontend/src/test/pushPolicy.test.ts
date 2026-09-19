import { describe, expect, it } from 'vitest';

import type { NotificationMediaFlags, PushDefaults } from '../types';
import { DEFAULT_NOTIFICATION_MEDIA } from '../types';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';
import { conversationIsEnabled, type ConversationOverrides } from '../utils/pushPolicy';

const DM_KEY = 'aa'.repeat(32);
const ROOM_KEY = 'bb'.repeat(32);
const HASHTAG_KEY = 'cc'.repeat(16);
const PRIVATE_CHAN_KEY = 'dd'.repeat(16);

const ON: NotificationMediaFlags = { ...DEFAULT_NOTIFICATION_MEDIA };
const PUSH_OFF: NotificationMediaFlags = { push: false, email: false, webhook: false };

const ALL_ON: PushDefaults = {
  new_contact: ON,
  new_dm: ON,
  advert_repeater: ON,
  advert_companion: ON,
  advert_sensor: ON,
  channel_found: ON,
  telemetry_alert: ON,
  oss_update: ON,
};

const DM_OFF: PushDefaults = {
  new_contact: ON,
  new_dm: PUSH_OFF,
  advert_repeater: ON,
  advert_companion: ON,
  advert_sensor: ON,
  channel_found: ON,
  telemetry_alert: ON,
  oss_update: ON,
};

type PolicyCase = {
  name: string;
  expected: boolean;
  stateKey: string;
  messageType: string;
  defaults?: PushDefaults;
  overrides?: ConversationOverrides;
  isHashtag?: boolean;
  isPublic?: boolean;
  channel?: 'push' | 'email' | 'webhook';
};

function policyCase(
  name: string,
  args: Omit<PolicyCase, 'name'>
): [string, boolean, Omit<PolicyCase, 'name' | 'expected'>] {
  const { expected, ...input } = args;
  return [name, expected, input];
}

/** Same case ids and expected outcomes as ``tests/test_push_policy.py`` POLICY_CASES. */
const POLICY_CASES = [
  policyCase('public_channel_no_override_enabled', {
    expected: true,
    stateKey: `channel-${PUBLIC_CHANNEL_KEY}`,
    messageType: 'CHAN',
    isPublic: true,
  }),
  policyCase('hashtag_channel_no_override_enabled', {
    expected: true,
    stateKey: `channel-${HASHTAG_KEY}`,
    messageType: 'CHAN',
    isHashtag: true,
  }),
  policyCase('private_channel_no_override_disabled', {
    expected: false,
    stateKey: `channel-${PRIVATE_CHAN_KEY}`,
    messageType: 'CHAN',
  }),
  policyCase('dm_priv_default_new_dm_true_enabled', {
    expected: true,
    stateKey: `contact-${DM_KEY}`,
    messageType: 'PRIV',
  }),
  policyCase('dm_priv_default_new_dm_false_disabled', {
    expected: false,
    stateKey: `contact-${DM_KEY}`,
    messageType: 'PRIV',
    defaults: DM_OFF,
  }),
  policyCase('room_priv_follows_new_dm_true_enabled', {
    expected: true,
    stateKey: `contact-${ROOM_KEY}`,
    messageType: 'PRIV',
  }),
  policyCase('room_priv_follows_new_dm_false_disabled', {
    expected: false,
    stateKey: `contact-${ROOM_KEY}`,
    messageType: 'PRIV',
    defaults: DM_OFF,
  }),
  policyCase('override_true_enables_private_channel', {
    expected: true,
    stateKey: `channel-${PRIVATE_CHAN_KEY}`,
    messageType: 'CHAN',
    overrides: { [`channel-${PRIVATE_CHAN_KEY}`]: true },
  }),
  policyCase('override_false_disables_public_channel', {
    expected: false,
    stateKey: `channel-${PUBLIC_CHANNEL_KEY}`,
    messageType: 'CHAN',
    isPublic: true,
    overrides: { [`channel-${PUBLIC_CHANNEL_KEY}`]: false },
  }),
  policyCase('override_false_disables_dm_despite_new_dm', {
    expected: false,
    stateKey: `contact-${DM_KEY}`,
    messageType: 'PRIV',
    overrides: { [`contact-${DM_KEY}`]: false },
  }),
  policyCase('override_true_enables_dm_despite_new_dm_off', {
    expected: true,
    stateKey: `contact-${DM_KEY}`,
    messageType: 'PRIV',
    defaults: DM_OFF,
    overrides: { [`contact-${DM_KEY}`]: true },
  }),
  policyCase('override_true_enables_room_despite_new_dm_off', {
    expected: true,
    stateKey: `contact-${ROOM_KEY}`,
    messageType: 'PRIV',
    defaults: DM_OFF,
    overrides: { [`contact-${ROOM_KEY}`]: true },
  }),
  policyCase('override_true_does_not_enable_email_when_matrix_off', {
    expected: false,
    stateKey: `contact-${DM_KEY}`,
    messageType: 'PRIV',
    overrides: { [`contact-${DM_KEY}`]: true },
    channel: 'email',
  }),
  policyCase('override_true_does_not_enable_email_for_public_channel', {
    expected: false,
    stateKey: `channel-${PUBLIC_CHANNEL_KEY}`,
    messageType: 'CHAN',
    isPublic: true,
    overrides: { [`channel-${PUBLIC_CHANNEL_KEY}`]: true },
    channel: 'email',
  }),
  policyCase('dm_email_follows_new_dm_matrix', {
    expected: true,
    stateKey: `contact-${DM_KEY}`,
    messageType: 'PRIV',
    defaults: {
      ...ALL_ON,
      new_dm: { push: true, email: true, webhook: false },
    },
    channel: 'email',
  }),
  policyCase('channel_email_override_enables_email', {
    expected: true,
    stateKey: `channel-${PUBLIC_CHANNEL_KEY}`,
    messageType: 'CHAN',
    isPublic: true,
    overrides: { [`channel-${PUBLIC_CHANNEL_KEY}`]: { email: true } },
    channel: 'email',
  }),
] as const;

describe('conversationIsEnabled', () => {
  it.each(POLICY_CASES)('%s', (_caseName, expected, input) => {
    expect(ALL_ON.new_dm.push).toBe(true);
    expect(
      conversationIsEnabled({
        defaults: ALL_ON,
        overrides: {},
        isHashtag: false,
        isPublic: false,
        ...input,
      })
    ).toBe(expected);
  });
});
