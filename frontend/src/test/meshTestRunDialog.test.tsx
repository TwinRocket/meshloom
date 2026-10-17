import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MeshTestRunDialog } from '../components/MeshTestRunDialog';
import i18n from '../i18n';

const regions = ['be', 'fr', 'nl-gr'];

function renderDialog(props: Partial<Parameters<typeof MeshTestRunDialog>[0]> = {}) {
  const base = {
    open: true,
    onOpenChange: vi.fn(),
    knownRegions: regions,
    floodScope: '#fr',
    sending: false,
    onRun: vi.fn(),
  };
  const view = render(<MeshTestRunDialog {...base} {...props} />);
  const rerender = (next: Partial<Parameters<typeof MeshTestRunDialog>[0]>) =>
    view.rerender(<MeshTestRunDialog {...base} {...props} {...next} />);
  return { ...base, ...props, rerender };
}

function checkedRegion(): string | null {
  const checked = screen
    .getAllByRole('radio')
    .find((radio) => radio.getAttribute('aria-checked') === 'true');
  return checked?.textContent ?? null;
}

describe('MeshTestRunDialog region preselect', () => {
  it('preselects the configured flood scope when it is a known region', () => {
    renderDialog();
    expect(checkedRegion()).toBe('fr');
  });

  it('falls back to the first known region when the flood scope is unknown', () => {
    renderDialog({ floodScope: '#de' });
    expect(checkedRegion()).toBe('be');
  });

  it('keeps the user pick across parent re-renders with the same inputs', () => {
    const { rerender, onRun } = renderDialog();
    fireEvent.click(screen.getByRole('radio', { name: 'nl-gr' }));
    rerender({ sending: false });
    expect(checkedRegion()).toBe('nl-gr');

    fireEvent.click(screen.getByRole('button', { name: i18n.t('meshTest.confirmRun') }));
    expect(onRun).toHaveBeenCalledWith('nl-gr');
  });

  it('preselects again on reopen, dropping the previous pick', () => {
    const { rerender } = renderDialog({ open: false });
    rerender({ open: true });
    expect(checkedRegion()).toBe('fr');

    fireEvent.click(screen.getByRole('radio', { name: 'be' }));
    rerender({ open: false });
    rerender({ open: true });
    expect(checkedRegion()).toBe('fr');
  });
});
