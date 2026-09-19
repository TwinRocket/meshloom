import type { PacketNetworkNode } from '../networkGraph/packetNetworkGraph';
import type { Contact, DirectoryHopHit } from '../types';
import { findContactsByPrefix, MIN_NAMED_HOP_HEX_CHARS } from './pathUtils';

export const DIRECTORY_RESOLVE_HOPS_MAX = 64;
export const DIRECTORY_RESOLVE_DEBOUNCE_MS = 400;

export type DirectoryLookupKind = 'self' | 'named' | 'hop' | 'pubkey' | 'other';

export interface DirectoryNodeLookup {
  kind: DirectoryLookupKind;
  /** Uppercase hex hop or 12-char pubkey prefix extracted from the node id. */
  token: string | null;
}

export type CommunityNameOverlay = {
  communityName: string;
  nameSource: 'community';
};

type DirectoryNameNode = Pick<
  PacketNetworkNode,
  'id' | 'type' | 'name' | 'probableIdentity' | 'isAmbiguous'
>;

export function parseVisualizerNodeLookup(nodeId: string): DirectoryNodeLookup {
  if (nodeId === 'self') {
    return { kind: 'self', token: null };
  }
  if (nodeId.startsWith('name:')) {
    return { kind: 'named', token: null };
  }

  const hopMatch = /^[?]([0-9a-f]+)(?::>[0-9a-f]+)?$/i.exec(nodeId);
  if (hopMatch) {
    return { kind: 'hop', token: hopMatch[1].toUpperCase() };
  }

  if (/^[0-9a-f]{12}$/i.test(nodeId)) {
    return { kind: 'pubkey', token: nodeId.toUpperCase() };
  }

  return { kind: 'other', token: null };
}

export function directoryPrefixesForLookup(lookup: DirectoryNodeLookup): string[] {
  if (lookup.kind === 'hop' && lookup.token) {
    if (lookup.token.length !== 4 && lookup.token.length !== 6) {
      return [];
    }
    return [lookup.token];
  }
  if (lookup.kind === 'pubkey' && lookup.token) {
    return [lookup.token.slice(0, 6), lookup.token.slice(0, 4)];
  }
  return [];
}

export function isEligibleForDirectoryName(node: DirectoryNameNode, contacts: Contact[]): boolean {
  if (node.probableIdentity) {
    return false;
  }

  const lookup = parseVisualizerNodeLookup(node.id);
  if (lookup.kind !== 'hop' && lookup.kind !== 'pubkey') {
    return false;
  }
  if (directoryPrefixesForLookup(lookup).length === 0) {
    return false;
  }

  if (lookup.kind === 'hop' && lookup.token) {
    if (lookup.token.length < MIN_NAMED_HOP_HEX_CHARS) {
      return false;
    }
    const matches = findContactsByPrefix(lookup.token, contacts, true);
    return matches.length === 0;
  }

  const contact = contacts.find(
    (candidate) => candidate.public_key.slice(0, 12).toUpperCase() === lookup.token
  );
  return !contact?.name;
}

export function collectDirectoryPrefixes(
  nodes: Iterable<DirectoryNameNode>,
  contacts: Contact[]
): string[] {
  const prefixes = new Set<string>();
  for (const node of nodes) {
    if (!isEligibleForDirectoryName(node, contacts)) {
      continue;
    }
    for (const prefix of directoryPrefixesForLookup(parseVisualizerNodeLookup(node.id))) {
      prefixes.add(prefix);
    }
  }
  return [...prefixes];
}

export function chunkDirectoryPrefixes(
  prefixes: string[],
  max = DIRECTORY_RESOLVE_HOPS_MAX
): string[][] {
  const chunks: string[][] = [];
  for (let i = 0; i < prefixes.length; i += max) {
    chunks.push(prefixes.slice(i, i + max));
  }
  return chunks;
}

export function normalizeDirectoryHits(
  resolved: Record<string, DirectoryHopHit>
): Record<string, DirectoryHopHit> {
  const normalized: Record<string, DirectoryHopHit> = {};
  for (const [prefix, hit] of Object.entries(resolved)) {
    normalized[prefix.toUpperCase()] = hit;
  }
  return normalized;
}

export function applyDirectoryName(
  node: DirectoryNameNode,
  resolved: Record<string, DirectoryHopHit>,
  contacts: Contact[]
): CommunityNameOverlay | null {
  if (!isEligibleForDirectoryName(node, contacts)) {
    return null;
  }

  const lookup = parseVisualizerNodeLookup(node.id);
  if (lookup.kind === 'hop' && lookup.token) {
    const hit = resolved[lookup.token];
    if (hit?.source === 'corescope' && hit.name) {
      return { communityName: hit.name, nameSource: 'community' };
    }
    return null;
  }

  if (lookup.kind === 'pubkey' && lookup.token) {
    const nodePrefix = node.id.toLowerCase();
    for (const prefix of directoryPrefixesForLookup(lookup)) {
      const hit = resolved[prefix];
      if (hit?.source !== 'corescope' || !hit.name || !hit.public_key) {
        continue;
      }
      if (hit.public_key.toLowerCase().startsWith(nodePrefix)) {
        return { communityName: hit.name, nameSource: 'community' };
      }
    }
  }

  return null;
}

export function buildCommunityNames(
  nodes: Iterable<DirectoryNameNode>,
  resolved: Record<string, DirectoryHopHit>,
  contacts: Contact[]
): Map<string, string> {
  const names = new Map<string, string>();
  for (const node of nodes) {
    const overlay = applyDirectoryName(node, resolved, contacts);
    if (overlay) {
      names.set(node.id, overlay.communityName);
    }
  }
  return names;
}
