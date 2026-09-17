import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SettingsIndexView } from '../components/SettingsIndexView';
import { SETTINGS_SECTION_LABELS } from '../components/settings/settingsConstants';
import i18n from '../i18n';

describe('SettingsIndexView', () => {
  it('badges Updates when an update is available, not About', () => {
    render(<SettingsIndexView onSelectSection={vi.fn()} updateAvailable />);

    const updatesRow = screen.getByRole('button', {
      name: new RegExp(i18n.t(SETTINGS_SECTION_LABELS.updates), 'i'),
    });
    expect(within(updatesRow).getByText(i18n.t('updates.badgeLabel'))).toBeInTheDocument();

    const aboutRow = screen.getByRole('button', {
      name: i18n.t(SETTINGS_SECTION_LABELS.about),
    });
    expect(within(aboutRow).queryByText(i18n.t('updates.badgeLabel'))).not.toBeInTheDocument();
  });

  it('does not badge Updates when no update is available', () => {
    render(<SettingsIndexView onSelectSection={vi.fn()} />);
    expect(screen.queryByText(i18n.t('updates.badgeLabel'))).not.toBeInTheDocument();
  });
});
