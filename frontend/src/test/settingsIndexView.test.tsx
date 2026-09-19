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

  it('lists alerts in the Radio group after radio-app', () => {
    render(<SettingsIndexView onSelectSection={vi.fn()} />);
    const radioHeading = screen.getByRole('heading', {
      name: i18n.t('settingsIndex.groupRadio'),
    });
    const group = radioHeading.closest('section');
    expect(group).not.toBeNull();
    const labels = within(group!)
      .getAllByRole('button')
      .map((button) => button.textContent ?? '');
    const radioAppIdx = labels.findIndex((label) =>
      label.includes(i18n.t(SETTINGS_SECTION_LABELS['radio-app']))
    );
    const alertsIdx = labels.findIndex((label) =>
      label.includes(i18n.t(SETTINGS_SECTION_LABELS.alerts))
    );
    expect(radioAppIdx).toBeGreaterThanOrEqual(0);
    expect(alertsIdx).toBe(radioAppIdx + 1);
  });
});
