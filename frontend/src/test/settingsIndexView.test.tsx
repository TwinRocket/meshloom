import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SettingsIndexView } from '../components/SettingsIndexView';
import { SETTINGS_SECTION_LABELS } from '../components/settings/settingsConstants';
import i18n from '../i18n';

describe('SettingsIndexView', () => {
  it('badges About when an update is available', () => {
    render(<SettingsIndexView onSelectSection={vi.fn()} updateAvailable />);

    expect(screen.getByText(i18n.t(SETTINGS_SECTION_LABELS.about))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('updates.badgeLabel'))).toBeInTheDocument();
  });

  it('does not badge About when no update is available', () => {
    render(<SettingsIndexView onSelectSection={vi.fn()} />);
    expect(screen.queryByText(i18n.t('updates.badgeLabel'))).not.toBeInTheDocument();
  });
});
