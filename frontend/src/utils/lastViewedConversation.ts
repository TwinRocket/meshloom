import i18n from '../i18n';
import type { Conversation } from '../types';
import { makeFeedConversation } from '../components/feed/feedConversation';
import { parseHashConversation } from './urlHash';

export const REOPEN_LAST_CONVERSATION_KEY = 'meshloom-reopen-last-conversation';
export const LAST_VIEWED_CONVERSATION_KEY = 'meshloom-last-viewed-conversation';

type StoredType = Conversation['type'] | 'feed';

const SUPPORTED_TYPES: StoredType[] = [
  'contact',
  'channel',
  'raw',
  'feed',
  'control',
  'map',
  'live',
  'visualizer',
  'trace',
  'locate',
  'discovered',
];

function isSupportedType(value: unknown): value is StoredType {
  return typeof value === 'string' && SUPPORTED_TYPES.includes(value as StoredType);
}

export function getReopenLastConversationEnabled(): boolean {
  try {
    return localStorage.getItem(REOPEN_LAST_CONVERSATION_KEY) === '1';
  } catch {
    return false;
  }
}

export function setReopenLastConversationEnabled(enabled: boolean): void {
  try {
    if (enabled) {
      localStorage.setItem(REOPEN_LAST_CONVERSATION_KEY, '1');
      return;
    }

    localStorage.removeItem(REOPEN_LAST_CONVERSATION_KEY);
    localStorage.removeItem(LAST_VIEWED_CONVERSATION_KEY);
  } catch {
    // localStorage may be unavailable
  }
}

export function saveLastViewedConversation(conversation: Conversation): void {
  try {
    localStorage.setItem(LAST_VIEWED_CONVERSATION_KEY, JSON.stringify(conversation));
  } catch {
    // localStorage may be unavailable
  }
}

export function getLastViewedConversation(): Conversation | null {
  try {
    const raw = localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw) as { type?: unknown; id?: unknown; name?: unknown } & Partial<
      Omit<Conversation, 'type' | 'id' | 'name'>
    > & { feedPreset?: unknown };
    if (
      !isSupportedType(parsed.type) ||
      typeof parsed.id !== 'string' ||
      typeof parsed.name !== 'string'
    ) {
      return null;
    }

    if (parsed.type === 'feed') {
      return makeFeedConversation(
        parsed.name,
        parsed.feedPreset === 'control' ? 'control' : undefined
      );
    }

    if (parsed.type === 'map') {
      return {
        type: 'map',
        id: parsed.id,
        name: parsed.name,
        ...(typeof parsed.mapFocusKey === 'string' && { mapFocusKey: parsed.mapFocusKey }),
      };
    }

    if (parsed.type === 'locate') {
      return {
        type: 'locate',
        id: parsed.id,
        name: parsed.name,
        ...(typeof parsed.locateKey === 'string' && { locateKey: parsed.locateKey }),
      };
    }

    return {
      type: parsed.type,
      id: parsed.id,
      name: parsed.name,
    };
  } catch {
    return null;
  }
}

export function captureLastViewedConversationFromHash(): void {
  const hashConversation = parseHashConversation();
  if (!hashConversation) return;

  if (hashConversation.type === 'raw') {
    saveLastViewedConversation({ type: 'raw', id: 'raw', name: 'Raw Packet Feed' });
    return;
  }
  if (hashConversation.type === 'feed') {
    saveLastViewedConversation(makeFeedConversation(i18n.t('sidebar.feed')));
    return;
  }
  if (hashConversation.type === 'control') {
    saveLastViewedConversation({
      type: 'control',
      id: 'control',
      name: i18n.t('sidebar.controlJournal'),
    });
    return;
  }
  if (hashConversation.type === 'map') {
    saveLastViewedConversation({
      type: 'map',
      id: 'map',
      name: 'Node Map',
      ...(hashConversation.mapFocusKey && { mapFocusKey: hashConversation.mapFocusKey }),
    });
    return;
  }
  if (hashConversation.type === 'live') {
    saveLastViewedConversation({ type: 'live', id: 'live', name: 'Live' });
    return;
  }
  if (hashConversation.type === 'visualizer') {
    saveLastViewedConversation({ type: 'visualizer', id: 'visualizer', name: 'Mesh Visualizer' });
    return;
  }
  if (hashConversation.type === 'trace') {
    saveLastViewedConversation({ type: 'trace', id: 'trace', name: 'Trace' });
    return;
  }
  if (hashConversation.type === 'locate') {
    saveLastViewedConversation({
      type: 'locate',
      id: 'locate',
      name: 'RF Locate',
      ...(hashConversation.locateKey && { locateKey: hashConversation.locateKey }),
    });
    return;
  }
  if (hashConversation.type === 'discovered') {
    saveLastViewedConversation({
      type: 'discovered',
      id: 'discovered',
      name: 'Discovered channels',
    });
    return;
  }

  saveLastViewedConversation({
    type: hashConversation.type,
    id: hashConversation.name,
    name: hashConversation.label || hashConversation.name,
  });
}
