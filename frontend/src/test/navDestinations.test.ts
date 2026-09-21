import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { Hash, Radio, ScrollText } from 'lucide-react';

import { RAIL_ITEMS } from '../components/navDestinations';

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('navDestinations live chrome', () => {
  it('uses Radio for the live rail entry, not CloudRain', () => {
    const live = RAIL_ITEMS.find((item) => item.id === 'live');
    expect(live?.Icon).toBe(Radio);
  });

  it('places the control journal next to the packet feed', () => {
    const rawIndex = RAIL_ITEMS.findIndex((item) => item.id === 'raw');
    const control = RAIL_ITEMS[rawIndex + 1];
    expect(control?.id).toBe('control');
    expect(control?.Icon).toBe(ScrollText);
    expect(control?.conversation).toEqual({ type: 'control', id: 'control', name: 'control' });
  });

  it('uses Hash for the discovered-channels tool', () => {
    const discovered = RAIL_ITEMS.find((item) => item.id === 'discovered');
    expect(discovered?.Icon).toBe(Hash);
    expect(discovered?.overlay).toBeUndefined();
    expect(discovered?.conversation?.type).toBe('discovered');
  });

  it('does not import CloudRain in live navigation components', () => {
    const paths = [
      'components/navDestinations.ts',
      'components/CommandPalette.tsx',
      'components/ToolsView.tsx',
    ];
    for (const rel of paths) {
      const source = readFileSync(join(frontendSrc, rel), 'utf8');
      expect(source).not.toMatch(/\bCloudRain\b/);
    }
  });
});
