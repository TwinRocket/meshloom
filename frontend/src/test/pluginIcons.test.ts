import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import { icons } from 'lucide-react';

/**
 * app/data/plugin_icons.json is the closed list of icon names a plugin manifest may
 * use (the manifest validator and its exported schema read it). Every name must be
 * a lucide icon this frontend ships, or the UI would have nothing to draw.
 */
const names: string[] = JSON.parse(
  readFileSync(resolve(__dirname, '../../../app/data/plugin_icons.json'), 'utf-8')
);

const pascal = (kebab: string) =>
  kebab
    .split('-')
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join('');

describe('plugin icon allowlist', () => {
  it('is a sorted list of unique kebab-case names', () => {
    expect(names.length).toBeGreaterThan(0);
    expect(names).toEqual([...new Set(names)].sort());
    for (const n of names) expect(n).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
  });

  it.each(names)('%s exists in lucide-react', (name) => {
    expect(Object.keys(icons)).toContain(pascal(name));
  });
});
