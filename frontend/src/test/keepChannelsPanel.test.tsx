import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { KeepChannelsPanel } from '../components/settings/SettingsRadioSection';
import i18n from '../i18n';
import type { AppSettings } from '../types';

const settings = (keep: boolean) => ({ keep_channels_on_radio: keep }) as unknown as AppSettings;

describe('KeepChannelsPanel (MSHL-41)', () => {
  it('is off by default and saves the setting when toggled', async () => {
    const onSave = vi.fn(async () => {});
    render(
      <KeepChannelsPanel
        appSettings={{} as AppSettings}
        maxChannels={8}
        onSaveAppSettings={onSave}
      />
    );
    const box = screen.getByRole('checkbox', {
      name: i18n.t('settings.radio.keepChannels.label'),
    });
    expect(box).not.toBeChecked();
    expect(
      screen.queryByText(i18n.t('settings.radio.keepChannels.slots', { count: 7 }), {
        exact: false,
      })
    ).toBeNull();
    fireEvent.click(box);
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ keep_channels_on_radio: true }));
  });

  it('says how many slots are kept: the limit minus the scratch slot', () => {
    render(
      <KeepChannelsPanel
        appSettings={settings(true)}
        maxChannels={8}
        onSaveAppSettings={vi.fn(async () => {})}
      />
    );
    expect(
      screen.getByText(i18n.t('settings.radio.keepChannels.slots', { count: 7 }), {
        exact: false,
      })
    ).toBeInTheDocument();
  });

  it('falls back to a generic note when the radio limit is unknown', () => {
    render(
      <KeepChannelsPanel
        appSettings={settings(true)}
        maxChannels={null}
        onSaveAppSettings={vi.fn(async () => {})}
      />
    );
    expect(
      screen.getByText(i18n.t('settings.radio.keepChannels.slotsUnknown'), { exact: false })
    ).toBeInTheDocument();
  });
});
