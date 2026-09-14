import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BottomNav } from '../components/BottomNav';
import i18n from '../i18n';

/**
 * The bar is the only primary navigation on a phone: installed, the app has no
 * address bar and no back button, so what it offers is what exists.
 */

function renderNav(overrides?: Partial<React.ComponentProps<typeof BottomNav>>) {
  const onSelect = vi.fn();
  render(<BottomNav active={null} unreadTotal={0} onSelect={onSelect} {...overrides} />);
  return { onSelect };
}

describe('BottomNav', () => {
  it('offers every top-level destination', () => {
    renderNav();
    for (const key of ['conversations', 'map', 'mesh', 'packets', 'settings']) {
      expect(screen.getByText(i18n.t(`bottomNav.${key}`))).toBeInTheDocument();
    }
  });

  it('reports the current destination to assistive technology', () => {
    renderNav({ active: 'map' });
    const current = screen
      .getAllByRole('button')
      .filter((b) => b.getAttribute('aria-current') === 'page');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent(i18n.t('bottomNav.map'));
  });

  it('marks nothing current when the view is not one of its destinations', () => {
    renderNav({ active: null });
    expect(
      screen.getAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'page')
    ).toHaveLength(0);
  });

  it('reports the destination that was chosen', () => {
    const { onSelect } = renderNav();
    fireEvent.click(screen.getByText(i18n.t('bottomNav.mesh')));
    expect(onSelect).toHaveBeenCalledWith('visualizer');
  });

  it('carries unread conversations as more than a colour', () => {
    renderNav({ unreadTotal: 3 });
    // The badge itself is decorative; the count has to reach a screen reader too.
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('bottomNav.unread', { count: 3 }))).toBeInTheDocument();
  });

  it('stops counting past a point rather than growing without limit', () => {
    renderNav({ unreadTotal: 150 });
    expect(screen.getByText('99+')).toBeInTheDocument();
  });

  it('shows no badge when everything is read', () => {
    renderNav({ unreadTotal: 0 });
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });
});
