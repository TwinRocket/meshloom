import type {
  NotificationKeywordInput,
  NotificationKeywordRule,
} from '../../../notifications/types';

export const KEYWORD_MAX_RULES = 20;
export const KEYWORD_MAX_PATTERN = 64;
export const KEYWORD_MAX_COOLDOWN_S = 86_400;
export const KEYWORD_DEFAULT_COOLDOWN_S = 300;
/** Web Push is decided per account: a keyword rule cannot name it (the server refuses it). */
export const KEYWORD_EXCLUDED_CHANNEL = 'builtin:push';

export type KeywordDraft = {
  id?: string;
  pattern: string;
  match: 'contains' | 'word';
  scopeKind: 'all' | 'dms' | 'chosen';
  scopeKeys: string[];
  channels: string[];
  cooldownMin: number;
};

export function emptyKeywordDraft(): KeywordDraft {
  return {
    pattern: '',
    match: 'contains',
    scopeKind: 'all',
    scopeKeys: [],
    channels: [],
    cooldownMin: KEYWORD_DEFAULT_COOLDOWN_S / 60,
  };
}

export function draftOf(rule: NotificationKeywordRule): KeywordDraft {
  return {
    id: rule.id,
    pattern: rule.pattern,
    match: rule.match,
    scopeKind: Array.isArray(rule.scope) ? 'chosen' : rule.scope,
    scopeKeys: Array.isArray(rule.scope) ? rule.scope : [],
    channels: rule.channels,
    cooldownMin: Math.round(rule.cooldown_s / 60),
  };
}

/** The pattern the server will store: trimmed, NFC. */
export function cleanPattern(value: string): string {
  return value.normalize('NFC').trim();
}

/** Why a draft cannot be saved (`null` when it can). */
export function draftError(draft: KeywordDraft): 'pattern' | 'scope' | 'cooldown' | null {
  const pattern = cleanPattern(draft.pattern);
  if (!pattern || [...pattern].length > KEYWORD_MAX_PATTERN) return 'pattern';
  if (draft.scopeKind === 'chosen' && draft.scopeKeys.length === 0) return 'scope';
  if (
    !Number.isInteger(draft.cooldownMin) ||
    draft.cooldownMin < 0 ||
    draft.cooldownMin * 60 > KEYWORD_MAX_COOLDOWN_S
  ) {
    return 'cooldown';
  }
  return null;
}

export function inputOf(draft: KeywordDraft): NotificationKeywordInput {
  return {
    ...(draft.id ? { id: draft.id } : {}),
    pattern: cleanPattern(draft.pattern),
    match: draft.match,
    scope: draft.scopeKind === 'chosen' ? draft.scopeKeys : draft.scopeKind,
    channels: draft.channels.filter((c) => c !== KEYWORD_EXCLUDED_CHANNEL),
    cooldown_s: draft.cooldownMin * 60,
  };
}

/** The list the PATCH sends after `draft` was saved: replaces its rule, or is appended. */
export function withDraft(
  rules: NotificationKeywordRule[],
  draft: KeywordDraft
): NotificationKeywordInput[] {
  const next = inputOf(draft);
  const list: NotificationKeywordInput[] = rules.map((r) => ({ ...r }));
  const at = draft.id ? list.findIndex((r) => r.id === draft.id) : -1;
  if (at >= 0) list[at] = next;
  else list.push(next);
  return list;
}

export function withoutRule(
  rules: NotificationKeywordRule[],
  id: string
): NotificationKeywordInput[] {
  return rules.filter((r) => r.id !== id).map((r) => ({ ...r }));
}
