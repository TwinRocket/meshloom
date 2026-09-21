import { useEffect, useMemo, useState } from 'react';

import { api } from '../api';

const EMPTY_NAMES: readonly string[] = [];

function mergeUniqueNames(communityNames: string[], extraNames: readonly string[]): string[] {
  const seen = new Set<string>();
  const merged: string[] = [];
  for (const name of [...communityNames, ...extraNames]) {
    if (!name || seen.has(name)) continue;
    seen.add(name);
    merged.push(name);
  }
  return merged;
}

/**
 * Community hashtag names for the finder wordlist.
 * Fetches names only while Community is enabled. No network when off.
 */
export function useCommunityHashtagNames(
  enabled: boolean,
  extraNames: readonly string[] = EMPTY_NAMES
): string[] {
  const [communityNames, setCommunityNames] = useState<string[]>([]);

  useEffect(() => {
    if (!enabled) {
      setCommunityNames([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const { hashtags } = await api.getCommunityHashtags();
        if (cancelled) return;
        setCommunityNames(hashtags.map((item) => item.name));
      } catch {
        if (!cancelled) setCommunityNames([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return useMemo(() => mergeUniqueNames(communityNames, extraNames), [communityNames, extraNames]);
}
