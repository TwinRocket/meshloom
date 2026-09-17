import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SettingsUpdatesSection } from '../components/settings/SettingsUpdatesSection';
import i18n from '../i18n';
import type { OssUpdateJob, OssUpdateStatus } from '../types';

const idleJob: OssUpdateJob = {
  state: 'idle',
  phase: null,
  percent: null,
  error: null,
  started_at: null,
};

function status(overrides: Partial<OssUpdateStatus> = {}): OssUpdateStatus {
  return {
    current: '3.2.0',
    latest: '3.3.0',
    update_available: true,
    html_url: 'https://example.invalid/release',
    install_kind: 'package',
    apply_supported: true,
    auto_update: false,
    auto_update_window_start: '02:00',
    auto_update_window_end: '05:00',
    auto_update_weekdays: [0, 1, 2, 3, 4],
    checked_at: 1_700_000_000,
    tz_name: 'Europe/Paris',
    next_auto_apply_at: 1_700_086_400,
    job: idleJob,
    ...overrides,
  };
}

describe('SettingsUpdatesSection', () => {
  it('renders status fields and check/install actions when apply is supported', () => {
    const onCheckNow = vi.fn();
    const onApply = vi.fn();
    render(<SettingsUpdatesSection updates={status()} onCheckNow={onCheckNow} onApply={onApply} />);

    expect(screen.getByText(i18n.t('settings.updates.status'))).toBeInTheDocument();
    expect(screen.getByText('v3.2.0')).toBeInTheDocument();
    expect(screen.getByText('v3.3.0')).toBeInTheDocument();
    expect(screen.getByText('Europe/Paris')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.updates.installKind.package'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.updates.jobState.idle'))).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.updates.nextAutoApply'), { exact: false })
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.updates.checkNow') }));
    expect(onCheckNow).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.updates.installNow') }));
    expect(onApply).toHaveBeenCalled();
  });

  it('hides install when no update is available', () => {
    render(
      <SettingsUpdatesSection
        updates={status({ update_available: false })}
        onCheckNow={vi.fn()}
        onApply={vi.fn()}
      />
    );

    expect(
      screen.queryByRole('button', { name: i18n.t('settings.updates.installNow') })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.updates.checkNow') })
    ).toBeInTheDocument();
  });

  it('patches auto-update, window, and weekdays', () => {
    const onPatchSettings = vi.fn();
    render(<SettingsUpdatesSection updates={status()} onPatchSettings={onPatchSettings} />);

    fireEvent.click(screen.getByRole('checkbox', { name: i18n.t('updates.autoUpdate') }));
    expect(onPatchSettings).toHaveBeenCalledWith({ auto_update: true });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.updates.weekday.5') }));
    expect(onPatchSettings).toHaveBeenCalledWith({ auto_update_weekdays: [0, 1, 2, 3, 4, 5] });

    const start = screen.getByLabelText(i18n.t('settings.updates.windowStart'));
    fireEvent.change(start, { target: { value: '03:15' } });
    fireEvent.blur(start);
    expect(onPatchSettings).toHaveBeenCalledWith({ auto_update_window_start: '03:15' });
  });

  it('shows Home Assistant help and no schedule when apply is unsupported on an addon', () => {
    render(
      <SettingsUpdatesSection
        updates={status({ apply_supported: false, install_kind: 'addon' })}
        onApply={vi.fn()}
      />
    );

    expect(screen.getByText(i18n.t('updates.addonManual'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('updates.autoUpdate'))).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.updates.installNow') })
    ).not.toBeInTheDocument();
  });

  it('shows manual help and recipes for container/source installs', () => {
    render(
      <SettingsUpdatesSection
        updates={status({ apply_supported: false, install_kind: 'source' })}
        onOpenManualHelp={vi.fn()}
      />
    );

    expect(screen.getByText(i18n.t('updates.manual'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('updates.packagesTitle'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('updates.dockerTitle'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('updates.autoUpdate'))).not.toBeInTheDocument();
  });
});
