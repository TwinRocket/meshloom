import type { TFunction } from 'i18next';

import type { JournalEntry, ThreadKind } from '../../utils/controlJournal';
import { controlThreadLabel } from './controlParts';

/** One node of the facet: a control thread with its packet count. */
export interface FeedNode {
  id: string;
  kind: ThreadKind;
  label: string;
  count: number;
  lastTimestamp: number;
}

export interface FeedNodeSection {
  kind: ThreadKind;
  nodes: FeedNode[];
}

const SECTION_ORDER: readonly ThreadKind[] = ['repeater', 'channel', 'us', 'unresolved'];

/** Threads of the control rows, grouped like the Control journal, newest activity first. */
export function buildFeedNodes(entries: readonly JournalEntry[], t: TFunction): FeedNodeSection[] {
  const byId = new Map<string, FeedNode>();
  for (const entry of entries) {
    const existing = byId.get(entry.threadId);
    if (existing) {
      existing.count += 1;
      existing.lastTimestamp = Math.max(existing.lastTimestamp, entry.packet.timestamp);
      continue;
    }
    byId.set(entry.threadId, {
      id: entry.threadId,
      kind: entry.threadKind,
      label: controlThreadLabel(t, entry),
      count: 1,
      lastTimestamp: entry.packet.timestamp,
    });
  }
  const nodes = [...byId.values()].sort((a, b) => b.lastTimestamp - a.lastTimestamp);
  return SECTION_ORDER.map((kind) => ({
    kind,
    nodes: nodes.filter((node) => node.kind === kind),
  })).filter((section) => section.nodes.length > 0);
}

/** Control entries the facet and the Control chip leave on screen. */
export function visibleControlEntries(
  entries: readonly JournalEntry[],
  showControl: boolean,
  node: string | null
): JournalEntry[] {
  if (!showControl) return [];
  return node ? entries.filter((entry) => entry.threadId === node) : [...entries];
}
