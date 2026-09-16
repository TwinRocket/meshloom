import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UpdateAvailableDialog } from '../components/UpdateAvailableDialog';
import i18n from '../i18n';
import type { OssUpdateStatus } from '../types';

const updates: OssUpdateStatus = {
  current: '1.0.0',
  latest: '1.1.0',
  update_available: true,
  html_url: 'https://github.com/TwinRocket/meshloom/releases/tag/v1.1.0',
};

describe('UpdateAvailableDialog', () => {
  it('shows both upgrade recipes and the release link', () => {
    render(<UpdateAvailableDialog open updates={updates} onClose={vi.fn()} />);

    expect(screen.getByText(i18n.t('updates.title'))).toBeInTheDocument();
    expect(screen.getByText(/sudo apt upgrade/)).toBeInTheDocument();
    expect(screen.getByText(/sudo dnf upgrade/)).toBeInTheDocument();
    expect(screen.getByText(/sudo docker compose pull/)).toBeInTheDocument();

    const notes = screen.getByRole('link', { name: i18n.t('updates.changelog') });
    expect(notes).toHaveAttribute('href', updates.html_url);
    expect(notes).toHaveAttribute('target', '_blank');
  });

  it('closes from the footer', () => {
    const onClose = vi.fn();
    render(<UpdateAvailableDialog open updates={updates} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('updates.close') }));
    expect(onClose).toHaveBeenCalled();
  });
});
