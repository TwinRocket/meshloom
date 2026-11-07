import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SettingsNavigationSection } from '../components/settings/SettingsNavigationSection';
import i18n from '../i18n';

describe('SettingsNavigationSection start page', () => {
  it('reads back the stored value and saves a change', () => {
    const onStartViewChange = vi.fn();
    render(
      <SettingsNavigationSection
        order={[]}
        onChange={vi.fn()}
        startView="feed"
        onStartViewChange={onStartViewChange}
      />
    );
    const select = screen.getByLabelText(
      i18n.t('settingsNavigation.startPage')
    ) as HTMLSelectElement;
    expect(select.value).toBe('feed');

    fireEvent.change(select, { target: { value: 'map' } });
    expect(onStartViewChange).toHaveBeenCalledWith('map');

    fireEvent.change(select, { target: { value: '' } });
    expect(onStartViewChange).toHaveBeenLastCalledWith('');
  });

  it('offers conversations first and is read-only for a non-admin', () => {
    render(
      <SettingsNavigationSection
        order={[]}
        onChange={vi.fn()}
        startView=""
        onStartViewChange={vi.fn()}
        startViewReadOnly
      />
    );
    const select = screen.getByLabelText(
      i18n.t('settingsNavigation.startPage')
    ) as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.options[0].value).toBe('');
    expect([...select.options].map((o) => o.value)).toContain('feed');
  });
});
