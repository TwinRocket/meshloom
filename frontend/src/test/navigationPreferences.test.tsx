import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import {
  addToRail,
  backfillRailOverlaysOnce,
  resolveRail,
  DEFAULT_RAIL,
  RAIL_ITEMS,
  ANCHORED_RAIL_IDS,
} from '../components/navDestinations';
import { SettingsNavigationSection } from '../components/settings/SettingsNavigationSection';
import i18n from '../i18n';

/**
 * The rail's contents are stored data, so what a stored value resolves to is a
 * contract — a stale or hand-edited list must still produce a usable rail.
 */

describe('resolveRail', () => {
  it('falls back to the permanent destinations when nothing is stored', () => {
    expect(resolveRail(undefined).map((i) => i.id)).toEqual(DEFAULT_RAIL);
    expect(resolveRail([]).map((i) => i.id)).toEqual(DEFAULT_RAIL);
  });

  it('keeps the stored order', () => {
    const stored = ['map', 'conversations'];
    expect(resolveRail(stored).map((i) => i.id)).toEqual(stored);
  });

  it('drops ids it does not recognise rather than rendering a hole', () => {
    const ids = resolveRail(['conversations', 'a-tool-that-was-removed', 'map']).map((i) => i.id);
    expect(ids).not.toContain('a-tool-that-was-removed');
    expect(ids).toContain('conversations');
  });

  it('ignores a duplicate instead of drawing the same entry twice', () => {
    const ids = resolveRail(['conversations', 'conversations', 'map']).map((i) => i.id);
    expect(ids.filter((id) => id === 'conversations')).toHaveLength(1);
  });

  it('puts back a permanent entry a stored list had dropped', () => {
    // Conversations and the map cannot be taken off the rail; a tool can, so a
    // stored list that omits one is a choice and is left alone.
    const ids = resolveRail(['raw']).map((i) => i.id);
    expect(ids).toContain('conversations');
    expect(ids).toContain('map');
    expect(ids).toContain('raw');
  });

  it('starts with everything on the rail', () => {
    // Removing what you do not use is easier to think of than adding what you
    // never saw, and the tools have no other entry on desktop.
    expect(DEFAULT_RAIL).toContain('live');
    expect(DEFAULT_RAIL).toContain('search');
    expect(DEFAULT_RAIL).toContain('cracker');
    expect(resolveRail([]).map((i) => i.id)).toEqual(DEFAULT_RAIL);
  });

  it('has no catalogue entry, which would be a second way to the same places', () => {
    expect(RAIL_ITEMS.map((i) => i.id)).not.toContain('tools');
  });

  it('leaves the bottom group out of the order entirely', () => {
    // Tools and settings are anchored, so the order never decides their rank and
    // adding a tool can never move them.
    for (const anchored of ANCHORED_RAIL_IDS) {
      expect(DEFAULT_RAIL).not.toContain(anchored);
      expect(RAIL_ITEMS.map((i) => i.id)).not.toContain(anchored);
      expect(resolveRail(['conversations']).map((i) => i.id)).not.toContain(anchored);
    }
  });

  it('carries the tools, which are what there is to add', () => {
    const optional = RAIL_ITEMS.filter((i) => !i.permanent).map((i) => i.id);
    expect(optional).toContain('live');
    expect(optional).toContain('raw');
    expect(optional).toContain('cracker');
    expect(resolveRail(['conversations', 'live']).map((i) => i.id)).toContain('live');
  });
});

function renderSection(order: string[]) {
  const onChange = vi.fn();
  render(<SettingsNavigationSection order={order} onChange={onChange} />);
  return { onChange };
}

const rowNames = () =>
  screen.getAllByRole('listitem').map((li) => within(li).getAllByText(/.+/)[0].textContent);

describe('SettingsNavigationSection', () => {
  it('reorders an entry without losing the others', () => {
    const { onChange } = renderSection(['conversations', 'map', 'live']);
    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('settingsNavigation.moveUp', { name: i18n.t('bottomNav.map') }),
      })
    );
    expect(onChange).toHaveBeenCalledWith(['map', 'conversations', 'live']);
  });

  it('adds back a tool that was taken off the rail', () => {
    const trimmed = DEFAULT_RAIL.filter((id) => id !== 'live');
    const { onChange } = renderSection(trimmed);
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('sidebar.live')) }));
    expect(onChange).toHaveBeenCalledWith([...trimmed, 'live']);
  });

  it('removes a tool but offers no way to remove a permanent destination', () => {
    const { onChange } = renderSection(DEFAULT_RAIL);
    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('settingsNavigation.remove', { name: i18n.t('sidebar.live') }),
      })
    );
    expect(onChange).toHaveBeenCalledWith(DEFAULT_RAIL.filter((id) => id !== 'live'));

    // A permanent entry has no remove control at all — not a disabled one.
    expect(
      screen.queryByRole('button', {
        name: i18n.t('settingsNavigation.remove', { name: i18n.t('bottomNav.conversations') }),
      })
    ).not.toBeInTheDocument();
  });

  it('cannot move the first entry up or the last one down', () => {
    renderSection(DEFAULT_RAIL);
    expect(
      screen.getByRole('button', {
        name: i18n.t('settingsNavigation.moveUp', { name: i18n.t('bottomNav.conversations') }),
      })
    ).toBeDisabled();
    const lastId = DEFAULT_RAIL[DEFAULT_RAIL.length - 1];
    const lastItem = RAIL_ITEMS.find((item) => item.id === lastId);
    expect(lastItem).toBeDefined();
    expect(
      screen.getByRole('button', {
        name: i18n.t('settingsNavigation.moveDown', {
          name: i18n.t(lastItem!.labelKey),
        }),
      })
    ).toBeDisabled();
  });

  it('restores the defaults', () => {
    const { onChange } = renderSection(['map', 'conversations', 'raw']);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settingsNavigation.reset') }));
    expect(onChange).toHaveBeenCalledWith(DEFAULT_RAIL);
    expect(rowNames().length).toBeGreaterThan(0);
  });
});

describe('channel finder overlay', () => {
  it('is on the default rail as an overlay, not a conversation', () => {
    const cracker = RAIL_ITEMS.find((item) => item.id === 'cracker');
    expect(cracker?.overlay).toBe(true);
    expect(cracker?.conversation).toBeUndefined();
    expect(cracker?.labelKey).toBe('sidebar.showChannelFinder');
  });

  it('backfills a stored rail that predates the overlay, once', () => {
    const stored = ['conversations', 'map', 'live'];
    const first = backfillRailOverlaysOnce(stored, false);
    expect(first.shouldPersist).toBe(true);
    expect(first.ids).toEqual(['conversations', 'map', 'live', 'cracker']);

    const afterRemoval = backfillRailOverlaysOnce(stored, true);
    expect(afterRemoval.shouldPersist).toBe(false);
    expect(afterRemoval.ids).toEqual(['conversations', 'map', 'live']);
  });

  it('does not persist when the defaults already include the overlay', () => {
    const empty = backfillRailOverlaysOnce([], false);
    expect(empty.ids).toContain('cracker');
    expect(empty.shouldPersist).toBe(false);
  });
});

describe('addToRail', () => {
  it('appends the entry', () => {
    expect(addToRail(['conversations', 'map'], 'live')).toEqual(['conversations', 'map', 'live']);
  });

  it('refuses to add the same entry twice', () => {
    const rail: ReturnType<typeof addToRail> = ['conversations', 'live'];
    expect(addToRail(rail, 'live')).toBe(rail);
  });
});

describe('rail icons', () => {
  it('gives every entry a glyph of its own', () => {
    // Trace and the visualizer both used Waypoints, so the rail drew the same icon
    // twice and neither could be told from the other at 20px.
    const icons = RAIL_ITEMS.map((item) => item.Icon);
    expect(new Set(icons).size).toBe(icons.length);
  });
});
