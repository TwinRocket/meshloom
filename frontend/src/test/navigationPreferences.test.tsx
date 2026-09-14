import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { resolveRail, DEFAULT_RAIL, RAIL_ITEMS } from '../components/navDestinations';
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
    const stored = ['settings', 'conversations', 'map', 'tools'];
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
    // Otherwise the rail can lose the only way back to where it is configured.
    const ids = resolveRail(['conversations']).map((i) => i.id);
    for (const permanent of DEFAULT_RAIL) expect(ids).toContain(permanent);
  });

  it('carries the tools, which are what there is to add', () => {
    const optional = RAIL_ITEMS.filter((i) => !i.permanent).map((i) => i.id);
    expect(optional).toContain('live');
    expect(optional).toContain('raw');
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
    const { onChange } = renderSection(['conversations', 'map', 'tools', 'settings']);
    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('settingsNavigation.moveUp', { name: i18n.t('bottomNav.map') }),
      })
    );
    expect(onChange).toHaveBeenCalledWith(['map', 'conversations', 'tools', 'settings']);
  });

  it('adds a tool to the end of the rail', () => {
    const { onChange } = renderSection(DEFAULT_RAIL);
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('sidebar.live')) }));
    expect(onChange).toHaveBeenCalledWith([...DEFAULT_RAIL, 'live']);
  });

  it('removes a tool but offers no way to remove a permanent destination', () => {
    const { onChange } = renderSection([...DEFAULT_RAIL, 'live']);
    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('settingsNavigation.remove', { name: i18n.t('sidebar.live') }),
      })
    );
    expect(onChange).toHaveBeenCalledWith(DEFAULT_RAIL);

    // A permanent entry has no remove control at all — not a disabled one.
    expect(
      screen.queryByRole('button', {
        name: i18n.t('settingsNavigation.remove', { name: i18n.t('bottomNav.settings') }),
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
    expect(
      screen.getByRole('button', {
        name: i18n.t('settingsNavigation.moveDown', { name: i18n.t('bottomNav.settings') }),
      })
    ).toBeDisabled();
  });

  it('restores the defaults', () => {
    const { onChange } = renderSection(['settings', 'conversations', 'map', 'tools', 'raw']);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settingsNavigation.reset') }));
    expect(onChange).toHaveBeenCalledWith(DEFAULT_RAIL);
    expect(rowNames().length).toBeGreaterThan(0);
  });
});
