import { describe, expect, it } from 'vitest';

import { parseGroupData } from './parseGroupData';

const CHANNEL_KEY = 'aabbccddeeff00112233445566778899';
const EXTRA_KEY = 'b9323ce55a729068ed8e3ed49b9b84b5';

const PACKET_OK = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';
const PAYLOAD_OK = '6ed356e5b542d4bceab6dc9bc995d8225492b0';
const PACKET_V1 = '59006e6c8154ac6dcf515d24b52c8c8ca4912cc700';
const PACKET_MAC_FAIL = '19006e062f38ab832d1a8b25d629e03c9b5b861018';
const PACKET_EXTRA = '190006816d19c267d7275a997f248a38e0966c9e2a';

describe('parseGroupData', () => {
  it('honors data_len and ignores AES padding', () => {
    const parsed = parseGroupData(PACKET_OK, [CHANNEL_KEY]);

    expect(parsed).not.toBeNull();
    expect(parsed?.data_type).toBe(0x00ab);
    expect(parsed?.data_len).toBe(9);
    expect(parsed?.data_hex).toBe(Buffer.from('sensor-ok').toString('hex'));
    expect(parsed?.data_text).toBe('sensor-ok');
  });

  it('accepts a payload envelope as well as a full packet', () => {
    expect(parseGroupData(PAYLOAD_OK, [CHANNEL_KEY])?.data_text).toBe('sensor-ok');
  });

  it('returns null when the MAC does not match', () => {
    expect(parseGroupData(PACKET_MAC_FAIL, [CHANNEL_KEY])).toBeNull();
  });

  it('skips packets whose payload_version is not 0', () => {
    expect(parseGroupData(PACKET_V1, [CHANNEL_KEY])).toBeNull();
  });

  it('returns null for a short packet', () => {
    expect(parseGroupData('1900', [CHANNEL_KEY])).toBeNull();
    expect(parseGroupData(new Uint8Array([0x01, 0x02]), [CHANNEL_KEY])).toBeNull();
  });

  it('returns null for a MeshCore packet that is not GroupData', () => {
    const groupTextPacket =
      '1500E69C7A89DD0AF6A2D69F5823B88F9720731E4B887C56932BF889255D8D926D99195927144323A42DD8A158F878B518B8304DF55E80501C7D02A9FFD578D3518283156BBA257BF8413E80A237393B2E4149BBBC864371140A9BBC4E23EB9BF203EF0D029214B3E3AAC3C0295690ACDB89A28619E7E5F22C83E16073AD679D25FA904D07E5ACF1DB5A7C77D7E1719FB9AE5BF55541EE0D7F59ED890E12CF0FEED6700818';

    expect(parseGroupData(groupTextPacket, [CHANNEL_KEY])).toBeNull();
  });

  it('tries extra secrets filtered by hash byte and uses the first MAC OK', () => {
    expect(parseGroupData(PACKET_EXTRA, [CHANNEL_KEY])).toBeNull();
    expect(parseGroupData(PACKET_EXTRA, [CHANNEL_KEY, EXTRA_KEY])?.data_text).toBe('hello');
    expect(parseGroupData(PACKET_OK, [EXTRA_KEY, CHANNEL_KEY])?.data_text).toBe('sensor-ok');
  });
});
