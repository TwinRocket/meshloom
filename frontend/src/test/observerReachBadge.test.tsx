import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ObserverReachBadge } from '../components/ObserverReachBadge';

describe('ObserverReachBadge', () => {
  it('hides when the count is not ready', () => {
    const { rerender } = render(
      <ObserverReachBadge state={{ status: 'loading' }} variant="inline" onOpen={() => {}} />
    );
    expect(screen.queryByTestId('observer-reach-badge')).not.toBeInTheDocument();

    rerender(<ObserverReachBadge state={{ status: 'ok', count: 0 }} variant="inline" onOpen={() => {}} />);
    expect(screen.queryByTestId('observer-reach-badge')).not.toBeInTheDocument();
  });

  it('shows the count and keeps the aria label current while rolling', () => {
    const { rerender } = render(
      <ObserverReachBadge state={{ status: 'ok', count: 2 }} variant="inline" onOpen={() => {}} />
    );
    expect(screen.getByTestId('observer-reach-count')).toHaveTextContent('2');
    expect(screen.getByTestId('observer-reach-badge')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('2')
    );

    rerender(
      <ObserverReachBadge state={{ status: 'ok', count: 3 }} variant="inline" onOpen={() => {}} />
    );
    expect(screen.getByTestId('observer-reach-badge')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('3')
    );
    expect(screen.getByTestId('observer-reach-count')).toHaveTextContent(/2|3/);
  });

  it('snaps the digits when the user prefers reduced motion', () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query) => {
      return {
        matches: query.includes('prefers-reduced-motion'),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      } as MediaQueryList;
    });

    const { rerender } = render(
      <ObserverReachBadge state={{ status: 'ok', count: 8 }} variant="header" onOpen={() => {}} />
    );
    rerender(
      <ObserverReachBadge state={{ status: 'ok', count: 12 }} variant="header" onOpen={() => {}} />
    );
    expect(screen.getByTestId('observer-reach-count')).toHaveTextContent('12');
  });
});
