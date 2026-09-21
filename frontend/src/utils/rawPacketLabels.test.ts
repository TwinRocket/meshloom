import { afterEach, describe, expect, it } from 'vitest';

import i18n from '../i18n';
import { DEFAULT_LOCALE } from './languagePreference';
import {
  labelField,
  labelHeaderField,
  labelPayloadType,
  labelRole,
  labelRoute,
  labelStatsBucket,
} from './rawPacketLabels';

describe('rawPacketLabels', () => {
  afterEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('maps decoder payload type names to live.legend-calqued labels', async () => {
    await i18n.changeLanguage('en');
    expect(labelPayloadType('Advert')).toBe('Advert');
    expect(labelPayloadType('GroupText')).toBe('Group text');
    expect(labelPayloadType('GroupData')).toBe('Group data');
    expect(labelPayloadType('TextMessage')).toBe('Text');
    expect(labelPayloadType('Ack')).toBe('Ack');
    expect(labelPayloadType('Request')).toBe('Request');
    expect(labelPayloadType('Response')).toBe('Response');
    expect(labelPayloadType('Trace')).toBe('Trace');
    expect(labelPayloadType('Path')).toBe('Path');
    expect(labelPayloadType('Control')).toBe('Control');
    expect(labelPayloadType('AnonRequest')).toBe('Anon request');
    expect(labelPayloadType('Multipart')).toBe('Multipart');
    expect(labelPayloadType('RawCustom')).toBe('Custom');
    expect(labelPayloadType('Unknown (0xf)')).toBe('Other');

    await i18n.changeLanguage('fr');
    expect(labelPayloadType('Advert')).toBe('Annonce');
    expect(labelPayloadType('GroupText')).toBe('Texte de groupe');
    expect(labelPayloadType('AnonRequest')).toBe('Requête anonyme');
    expect(labelPayloadType('RawCustom')).toBe('Personnalisé');
    expect(labelPayloadType('Unknown')).toBe('Autre');
  });

  it('maps decoder route names', async () => {
    await i18n.changeLanguage('en');
    expect(labelRoute('Flood')).toBe('Flood');
    expect(labelRoute('Direct')).toBe('Direct');
    expect(labelRoute('TransportFlood')).toBe('Transport flood');
    expect(labelRoute('TransportDirect')).toBe('Transport direct');
    expect(labelRoute('Unknown (3)')).toBe('Unknown');

    await i18n.changeLanguage('fr');
    expect(labelRoute('TransportFlood')).toBe('Flood transport');
    expect(labelRoute('Unknown')).toBe('Inconnu');
  });

  it('maps advert roles from decoder and live.nodes names', async () => {
    await i18n.changeLanguage('en');
    expect(labelRole('Repeater')).toBe('Repeater');
    expect(labelRole('Room Server')).toBe('Room');
    expect(labelRole('Chat Node')).toBe('Companion');
    expect(labelRole('Companion')).toBe('Companion');
    expect(labelRole('Sensor')).toBe('Sensor');
    expect(labelRole('Unknown')).toBe('Unknown role');

    await i18n.changeLanguage('fr');
    expect(labelRole('Repeater')).toBe('Répéteur');
    expect(labelRole('Room')).toBe('Salon');
    expect(labelRole('Client')).toBe('Companion');
    expect(labelRole('Sensor')).toBe('Capteur');
  });

  it('translates decoder field names and falls back to the original English', async () => {
    await i18n.changeLanguage('en');
    expect(labelField('Header')).toBe('Header');
    expect(labelField('Path Data')).toBe('Path Data');
    expect(labelField('Ciphertext')).toBe('Ciphertext');
    expect(labelField('Channel Hash')).toBe('Channel Hash');
    expect(labelField('Destination Hash')).toBe('Destination Hash');
    expect(labelField('Source Hash')).toBe('Source Hash');
    expect(labelField('Sender Public Key')).toBe('Sender Public Key');
    expect(labelField('MAC')).toBe('MAC');
    expect(labelField('Payload')).toBe('Payload');
    expect(labelField('Invented Field')).toBe('Invented Field');

    await i18n.changeLanguage('fr');
    expect(labelField('Header')).toBe('En-tête');
    expect(labelField('Path Data')).toBe('Données de chemin');
    expect(labelField('Channel Hash')).toBe('Hash de canal');
    expect(labelField('Sender Public Key')).toBe('Clé publique de l’expéditeur');
    expect(labelField('Invented Field')).toBe('Invented Field');
  });

  it('translates header breakdown labels', async () => {
    await i18n.changeLanguage('en');
    expect(labelHeaderField('Route Type')).toBe('Route Type');
    expect(labelHeaderField('Payload Type')).toBe('Payload Type');
    expect(labelField('Version')).toBe('Version');
    expect(labelHeaderField('Hash Size')).toBe('Hash Size');
    expect(labelHeaderField('Hop Count')).toBe('Hop Count');

    await i18n.changeLanguage('fr');
    expect(labelHeaderField('Route Type')).toBe('Type de route');
    expect(labelField('Payload Type')).toBe('Type de charge utile');
    expect(labelHeaderField('Unknown Header')).toBe('Unknown Header');
  });

  it('translates raw packet stats buckets and keeps unknown English', async () => {
    await i18n.changeLanguage('en');
    expect(labelStatsBucket('No path')).toBe('No path');
    expect(labelStatsBucket('Strong (>-70 dBm)')).toBe('Strong (>-70 dBm)');
    expect(labelStatsBucket('Okay (-70 to -85 dBm)')).toBe('Okay (-70 to -85 dBm)');
    expect(labelStatsBucket('Weak (<-85 dBm)')).toBe('Weak (<-85 dBm)');
    expect(labelStatsBucket('1 byte / hop')).toBe('1 byte / hop');

    await i18n.changeLanguage('fr');
    expect(labelStatsBucket('No path')).toBe('Aucun chemin');
    expect(labelStatsBucket('Strong (>-70 dBm)')).toBe('Fort (>-70 dBm)');
    expect(labelStatsBucket('Mystery bucket')).toBe('Mystery bucket');
  });
});
