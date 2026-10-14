import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RadioFirmwareSettings } from '../components/settings/RadioFirmwareSettings';
import { api } from '../api';
import i18n from '../i18n';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

describe('RadioFirmwareSettings', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('shows the GPS toggle and saves it', async () => {
    vi.spyOn(api, 'getRadioCustomVars').mockResolvedValue({ supported: true, vars: { gps: '1' } });
    const set = vi
      .spyOn(api, 'setRadioCustomVar')
      .mockResolvedValue({ supported: true, vars: { gps: '0' } });
    render(<RadioFirmwareSettings />);

    const box = await screen.findByRole('checkbox', { name: 'GPS' });
    expect(box).toBeChecked();
    await userEvent.click(box);

    await waitFor(() => expect(set).toHaveBeenCalledWith('gps', '0'));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'GPS' })).not.toBeChecked());
  });

  it('shows the error when the radio refuses', async () => {
    vi.spyOn(api, 'getRadioCustomVars').mockResolvedValue({ supported: true, vars: { gps: '0' } });
    vi.spyOn(api, 'setRadioCustomVar').mockRejectedValue(new Error('Radio rejected setting gps'));
    render(<RadioFirmwareSettings />);

    await userEvent.click(await screen.findByRole('checkbox', { name: 'GPS' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Radio rejected setting gps');
  });

  it('saves an unknown variable from its text input', async () => {
    vi.spyOn(api, 'getRadioCustomVars').mockResolvedValue({ supported: true, vars: { foo: 'a' } });
    const set = vi
      .spyOn(api, 'setRadioCustomVar')
      .mockResolvedValue({ supported: true, vars: { foo: 'ab' } });
    render(<RadioFirmwareSettings />);

    await userEvent.type(await screen.findByLabelText('foo'), 'b');
    await userEvent.click(
      screen.getByRole('button', {
        name: i18n.t('settings.radio.firmware.saveVar', { name: 'foo' }),
      })
    );
    await waitFor(() => expect(set).toHaveBeenCalledWith('foo', 'ab'));
  });

  it('says the firmware reports no settings when unsupported', async () => {
    vi.spyOn(api, 'getRadioCustomVars').mockResolvedValue({ supported: false, vars: {} });
    render(<RadioFirmwareSettings />);
    expect(await screen.findByText(i18n.t('settings.radio.firmware.unsupported'))).toBeVisible();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('explains how to get a GPS listed when there is no setting', async () => {
    vi.spyOn(api, 'getRadioCustomVars').mockResolvedValue({ supported: true, vars: {} });
    render(<RadioFirmwareSettings />);
    expect(await screen.findByText(i18n.t('settings.radio.firmware.empty'))).toBeVisible();
  });
});
