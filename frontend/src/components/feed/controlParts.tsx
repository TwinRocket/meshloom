import type { TFunction } from 'i18next';

import type { Contact } from '../../types';
import { destMatchesPublicKey, type JournalEntry } from '../../utils/controlJournal';
import { getContactDisplayName } from '../../utils/pubkey';

/** Pieces of a control card shared by the Control journal and the feed's control rows. */

export function formatTypeHex(value: number): string {
  return `0x${value.toString(16).padStart(4, '0')}`;
}

export function peerLabel(
  contacts: Contact[],
  hash: string | null,
  isUs: boolean,
  usLabel: string,
  unknownLabel: string,
  ambiguousLabel: (hash: string) => string
): string {
  if (isUs) return usLabel;
  if (!hash) return unknownLabel;
  if (contacts.length === 1) {
    return getContactDisplayName(contacts[0].name, contacts[0].public_key, contacts[0].last_advert);
  }
  if (contacts.length > 1) return ambiguousLabel(hash);
  return hash;
}

export function PeerName({
  label,
  contact,
  unique,
  onOpenContactInfo,
}: {
  label: string;
  contact?: Contact;
  unique: boolean;
  onOpenContactInfo?: (publicKey: string, fromChannel?: boolean) => void;
}) {
  if (!unique || !contact || !onOpenContactInfo) {
    return <span>{label}</span>;
  }
  return (
    <span
      role="link"
      tabIndex={0}
      className="cursor-pointer text-primary hover:underline"
      onClick={(event) => {
        event.stopPropagation();
        onOpenContactInfo(contact.public_key);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          onOpenContactInfo(contact.public_key);
        }
      }}
    >
      {label}
    </span>
  );
}

export function controlSubtypeLabel(t: TFunction, entry: JournalEntry): string | null {
  if (entry.journalType === 'AnonRequest') return t('controlJournal.subtypeLogin');
  if (entry.journalType === 'GroupData') return t('controlJournal.subtypeDatagram');
  if (!entry.requestSubtype) return null;
  const folded = entry.requestSubtype.toLowerCase().replace(/[\s_()-]+/g, '');
  if (folded.includes('stats')) return t('controlJournal.subtypeStatus');
  if (folded.includes('owner')) return t('controlJournal.subtypeOwner');
  if (folded.includes('region')) return t('controlJournal.subtypeRegions');
  return entry.requestSubtype;
}

export function controlFromLabel(t: TFunction, entry: JournalEntry, publicKey?: string): string {
  if (entry.anonSender) {
    return getContactDisplayName(
      entry.anonSender.name,
      entry.anonSender.public_key,
      entry.anonSender.last_advert
    );
  }
  if (entry.anonSenderKey) return entry.anonSenderKey.slice(0, 12).toUpperCase();
  return peerLabel(
    entry.srcContacts,
    entry.srcHash,
    Boolean(entry.srcHash && publicKey && destMatchesPublicKey(entry.srcHash, publicKey)),
    t('controlJournal.usThread'),
    t('controlJournal.unknownPeer'),
    (hash) => t('controlJournal.ambiguousHash', { hash })
  );
}

export function controlToLabel(t: TFunction, entry: JournalEntry): string {
  return peerLabel(
    entry.destContacts,
    entry.destHash,
    entry.destIsUs,
    t('controlJournal.usThread'),
    t('controlJournal.unknownPeer'),
    (hash) => t('controlJournal.ambiguousHash', { hash })
  );
}

/** The locked envelope's "Dest X · Src Y" line, or null when neither hash is known. */
export function controlEnvelopeLabel(t: TFunction, entry: JournalEntry): string | null {
  if (entry.destHash && entry.srcHash) {
    return t('controlJournal.destSrc', { dest: entry.destHash, src: entry.srcHash });
  }
  if (entry.destHash) return t('controlJournal.destOnly', { dest: entry.destHash });
  if (entry.srcHash) return t('controlJournal.srcOnly', { src: entry.srcHash });
  return null;
}

/** The label of the thread an entry opens (the repeater's name, the channel's name, ...). */
export function controlThreadLabel(t: TFunction, entry: JournalEntry): string {
  if (entry.threadKind === 'us') return t('controlJournal.usThread');
  if (entry.threadKind === 'channel') {
    return entry.channel?.name ?? t('controlJournal.unresolvedThread');
  }
  if (entry.threadKind === 'repeater' && entry.destContacts[0]) {
    const contact = entry.destContacts[0];
    return getContactDisplayName(contact.name, contact.public_key, contact.last_advert);
  }
  return t('controlJournal.unresolvedThread');
}
