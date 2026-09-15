import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { Radio } from 'lucide-react';

import { RAIL_ITEMS } from '../components/navDestinations';

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('navDestinations live chrome', () => {
  it('uses Radio for the live rail entry, not CloudRain', () => {
    const live = RAIL_ITEMS.find((item) => item.id === 'live');
    expect(live?.Icon).toBe(Radio);
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
