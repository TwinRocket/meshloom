/**
 * Public key utilities for consistent handling of 64-char full keys
 * and 12-char prefixes throughout the application.
 *
 * MeshCore uses 64-character hex strings for public keys, but messages
 * and some radio operations only provide 12-character prefixes. This
 * module provides utilities for working with both formats consistently.
 */

import type { Contact } from '../types';

/** Length of a public key prefix in hex characters */
const PUBKEY_PREFIX_LENGTH = 12;

/**
 * Extract the 12-character prefix from a public key.
 * Works with both full keys and existing prefixes.
 */
function getPubkeyPrefix(key: string): string {
  return key.slice(0, PUBKEY_PREFIX_LENGTH);
}

/**
 * Get a display name for a contact, falling back to pubkey prefix.
 */
export function getContactDisplayName(
  name: string | null | undefined,
  pubkey: string,
  lastAdvert?: number | null
): string {
  if (name) return name;
  if (isUnknownFullKeyContact(pubkey, lastAdvert)) return '[unknown sender]';
  return getPubkeyPrefix(pubkey);
}

export function isPrefixOnlyContact(pubkey: string): boolean {
  return pubkey.length < 64;
}

export function isUnknownFullKeyContact(pubkey: string, lastAdvert?: number | null): boolean {
  return pubkey.length === 64 && !lastAdvert;
}

export function resolveContactsByPrefix(hash: string, contacts: Contact[]): Contact[] {
  const needle = hash
    .trim()
    .replace(/^hash1:/i, '')
    .toLowerCase();
  if (!needle) return [];
  return contacts.filter((contact) => contact.public_key.toLowerCase().startsWith(needle));
}
