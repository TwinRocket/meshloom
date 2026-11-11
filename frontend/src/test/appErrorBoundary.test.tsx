import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AppErrorBoundary } from '../components/AppErrorBoundary';

function Boom(): never {
  throw new Error('boom');
}

describe('AppErrorBoundary', () => {
  it('shows a recovery prompt instead of a blank page', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <AppErrorBoundary compact>
        <Boom />
      </AppErrorBoundary>
    );
    expect(screen.getByTestId('app-error-boundary')).toBeInTheDocument();
    spy.mockRestore();
  });

  it('renders children when nothing fails', () => {
    render(
      <AppErrorBoundary>
        <p>fine</p>
      </AppErrorBoundary>
    );
    expect(screen.getByText('fine')).toBeInTheDocument();
  });
});
