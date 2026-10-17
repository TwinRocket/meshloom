// @vitest-environment node
import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import postcss, { type AtRule, type Root, type Rule } from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

/**
 * Guards for the Tailwind 4 stylesheet, compiled the way `vite build` compiles it.
 *
 * Fonts: the app owns `--font-sans`/`--font-mono` (index.css `:root`, overridden
 * per theme), and Tailwind's `font-sans`/`font-mono` append 'Meshloom Marks', the
 * fallback for stacked combining marks no Windows font covers. The upgrade tool
 * wrote `--font-mono: var(--font-mono), 'Meshloom Marks'` as a plain theme
 * variable: a self-reference the app's `:root` overwrites, so the fallback
 * silently vanished from every `font-mono`.
 *
 * Layers: an unlayered rule beats every utility whatever its specificity. Only
 * overrides of unlayered third-party stylesheets may stay unlayered.
 */

const SRC = join(__dirname, '..');
const read = (name: string) => readFileSync(join(SRC, name), 'utf8');

let compiled: Root;

beforeAll(async () => {
  const result = await postcss([tailwindcss({ base: SRC, optimize: false })]).process(
    read('index.css'),
    { from: join(SRC, 'index.css') }
  );
  compiled = result.root;
}, 60000);

function declsFor(root: Root, selector: string, prop: string): string[] {
  const values: string[] = [];
  root.walkRules((rule: Rule) => {
    if (rule.selectors.includes(selector)) {
      rule.walkDecls(prop, (decl) => {
        values.push(decl.value);
      });
    }
  });
  return values;
}

describe('Tailwind font tokens', () => {
  it.each(['.font-sans', '.font-mono'])('%s ends with the Meshloom Marks fallback', (selector) => {
    const values = declsFor(compiled, selector, 'font-family');
    expect(values.length).toBeGreaterThan(0);
    for (const value of values) {
      expect(value).toMatch(/^var\(--font-(sans|mono)\), 'Meshloom Marks'$/);
    }
  });

  it('Preflight html and code fonts keep the fallback', () => {
    const vars = new Map<string, string>();
    compiled.walkDecls(/^--default-(mono-)?font-family$/, (decl) => {
      vars.set(decl.prop, decl.value);
    });
    expect(vars.get('--default-font-family')).toContain("'Meshloom Marks'");
    expect(vars.get('--default-mono-font-family')).toContain("'Meshloom Marks'");
  });

  it('no theme variable references itself', () => {
    const selfRefs: string[] = [];
    compiled.walkDecls(/^--/, (decl) => {
      if (decl.value.includes(`var(${decl.prop})`)) selfRefs.push(`${decl.prop}: ${decl.value}`);
    });
    expect(selfRefs).toEqual([]);
  });
});

describe('Tailwind 3 parity', () => {
  it('space-y keeps the Tailwind 3 sibling margin and neutralises the Tailwind 4 rule', () => {
    // Tailwind 4 puts the gap on the end margin of every child but the last, which an
    // inline <label> ignores. index.css restores the Tailwind 3 rule and makes the
    // reverse variable invalid on the children, so Tailwind's own margins compute to
    // zero. If Tailwind renames that variable, the neutralisation silently stops.
    expect(
      declsFor(compiled, '.space-y-2 > :not([hidden]) ~ :not([hidden])', 'margin-top')
    ).toEqual(['calc(var(--spacing) * 2)']);
    expect(declsFor(compiled, '.space-y-2 > *', '--tw-space-y-reverse')).toEqual(['none']);
    const core = declsFor(compiled, ':where(.space-y-2 > :not(:last-child))', 'margin-block-end');
    expect(core.length).toBeGreaterThan(0);
    for (const value of core) expect(value).toContain('var(--tw-space-y-reverse)');
  });

  it('text sizes keep length line heights, which inherit as lengths', () => {
    const vars = new Map<string, string>();
    compiled.walkDecls(/^--text-(xs|sm|base|lg)--line-height$/, (decl) => {
      vars.set(decl.prop, decl.value);
    });
    expect(vars.get('--text-xs--line-height')).toBe('1rem');
    expect(vars.get('--text-sm--line-height')).toBe('1.25rem');
  });

  it('every palette shade the app uses keeps its Tailwind 3 sRGB value', () => {
    const oklch: string[] = [];
    compiled.walkDecls(/^--color-[a-z]+-\d+$/, (decl) => {
      if (decl.value.startsWith('oklch')) oklch.push(decl.prop);
    });
    expect(oklch).toEqual([]);
  });
});

describe('cascade layers', () => {
  const THIRD_PARTY = /\.(leaflet-|maplibregl-|cm-|observer-reach-hop-label|neighbor-map-label)/;

  it.each(['index.css', 'themes.css', 'styles.css'])(
    '%s leaves only third-party overrides unlayered',
    (file) => {
      const root = postcss.parse(read(file));
      const unlayered: string[] = [];
      root.walkRules((rule: Rule) => {
        let parent = rule.parent;
        while (parent && parent.type !== 'root') {
          if (parent.type === 'atrule') {
            const name = (parent as AtRule).name;
            if (['layer', 'keyframes', 'theme', 'utility'].includes(name)) return;
          }
          parent = parent.parent;
        }
        if (!THIRD_PARTY.test(rule.selector)) unlayered.push(rule.selector);
      });
      expect(unlayered).toEqual([]);
    }
  );

  it('keeps no global margin/padding reset outside Preflight', () => {
    // Unlayered, `* { margin: 0; padding: 0 }` beat every p-, m- and space- utility.
    for (const file of ['index.css', 'themes.css', 'styles.css']) {
      const root = postcss.parse(read(file));
      root.walkRules('*', (rule) => {
        rule.walkDecls(/^(margin|padding)$/, (decl) => {
          throw new Error(`${file}: * { ${decl.prop} } duplicates Preflight`);
        });
      });
    }
  });
});
