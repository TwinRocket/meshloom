import i18n from '../i18n';

const PAYLOAD_TYPE_KEYS: Record<string, string> = {
  advert: 'advert',
  grouptext: 'groupText',
  groupdata: 'groupData',
  textmessage: 'textMessage',
  text: 'textMessage',
  ack: 'ack',
  request: 'request',
  req: 'request',
  response: 'response',
  trace: 'trace',
  path: 'path',
  control: 'control',
  anonrequest: 'anonRequest',
  multipart: 'multipart',
  rawcustom: 'rawCustom',
  custom: 'rawCustom',
  unknown: 'unknown',
  other: 'unknown',
  grptxt: 'groupText',
  grpdata: 'groupData',
  anonreq: 'anonRequest',
};

const ROUTE_KEYS: Record<string, string> = {
  flood: 'flood',
  direct: 'direct',
  transportflood: 'transportFlood',
  transportdirect: 'transportDirect',
  unknown: 'unknown',
};

const ROLE_KEYS: Record<string, string> = {
  repeater: 'repeater',
  room: 'room',
  roomserver: 'room',
  client: 'client',
  companion: 'companion',
  chatnode: 'client',
  sensor: 'sensor',
  unknown: 'unknown',
  unknownrole: 'unknown',
};

function foldLookupKey(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, '');
}

function lookupMappedKey(name: string, map: Record<string, string>): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return undefined;
  return map[foldLookupKey(trimmed)] ?? map[trimmed.toLowerCase()];
}

function translateOrFallback(key: string, fallback: string): string {
  if (!i18n.exists(key)) return fallback;
  const translated = i18n.t(key);
  return translated === key ? fallback : translated;
}

function unknownish(name: string): boolean {
  return /^unknown(\b|\s|$|\()/i.test(name.trim());
}

export function labelPayloadType(name: string): string {
  const mapped = lookupMappedKey(name, PAYLOAD_TYPE_KEYS);
  if (mapped) return i18n.t(`rawPacket.type.${mapped}`);
  if (unknownish(name)) return i18n.t('rawPacket.type.unknown');
  return name;
}

export function labelRoute(name: string): string {
  const mapped = lookupMappedKey(name, ROUTE_KEYS);
  if (mapped) return i18n.t(`rawPacket.route.${mapped}`);
  if (unknownish(name)) return i18n.t('rawPacket.route.unknown');
  return name;
}

export function labelRole(name: string): string {
  const mapped = lookupMappedKey(name, ROLE_KEYS);
  if (mapped) return i18n.t(`rawPacket.role.${mapped}`);
  if (unknownish(name)) return i18n.t('rawPacket.role.unknown');
  return name;
}

export function labelField(englishName: string): string {
  const fieldKey = `rawPacket.field.${englishName}`;
  if (i18n.exists(fieldKey)) return i18n.t(fieldKey);
  return labelHeaderField(englishName);
}

export function labelHeaderField(englishName: string): string {
  return translateOrFallback(`rawPacket.header.${englishName}`, englishName);
}

export function labelStatsBucket(englishName: string): string {
  return translateOrFallback(`rawPacket.statsBucket.${englishName}`, englishName);
}
