import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { RadioStatusDialog } from '../components/RadioStatusDialog';
import i18n from '../i18n';
import type { HealthStatus, RadioConfig } from '../types';

/**
 * The rail status is enough to notice something is wrong and never enough to
 * act on it. This is what it summarises, so what it says has to be answerable,
 * including sending an advert without opening the radio settings.
 */

const health = {
  radio_connected: true,
  radio_state: 'connected',
  connection_info: 'TCP: 192.168.1.204:5051',
  radio_device_info: { model: 'Heltec V4.3 OLED', firmware_version: 'v1.17.1' },
  radio_proxy: { enabled: true, port: 5051 },
  app_info: { version: '4.7.1' },
} as unknown as HealthStatus;

const config = { name: 'Pascal' } as RadioConfig;

function open(overrides?: Partial<React.ComponentProps<typeof RadioStatusDialog>>) {
  const onClose = vi.fn();
  const onOpenRadioSettings = vi.fn();
  const onAdvertise = vi.fn(async () => {});
  render(
    <RadioStatusDialog
      open
      health={health}
      config={config}
      onClose={onClose}
      onOpenRadioSettings={onOpenRadioSettings}
      onAdvertise={onAdvertise}
      {...overrides}
    />
  );
  return { onClose, onOpenRadioSettings, onAdvertise };
}

describe('RadioStatusDialog', () => {
  it('answers which radio, over what, and through what', () => {
    open();
    expect(screen.getByText('Pascal')).toBeInTheDocument();
    expect(screen.getByText('TCP: 192.168.1.204:5051')).toBeInTheDocument();
    expect(screen.getByText('Heltec V4.3 OLED')).toBeInTheDocument();
    expect(screen.getByText('v1.17.1')).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('radioStatus.proxyListening', { port: 5051 }))
    ).toBeInTheDocument();
  });

  it('states the condition in words, not only as a coloured dot', () => {
    open();
    expect(screen.getByText(i18n.t('statusBar.radioOk'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('radioStatus.connectedHelp'))).toBeInTheDocument();
  });

  it('says what a disconnected radio means for what you can do', () => {
    open({ health: { radio_connected: false, connection_info: null } as HealthStatus });
    expect(screen.getByText(i18n.t('statusBar.radioDisconnected'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('radioStatus.disconnectedHelp'))).toBeInTheDocument();
  });

  it('says so when no transport is configured rather than showing an empty row', () => {
    open({
      health: {
        radio_connected: false,
        transport_configured: false,
        connection_info: null,
      } as HealthStatus,
    });
    expect(screen.getByText(i18n.t('radioStatus.notConfigured'))).toBeInTheDocument();
  });

  it('offers the settings without being the only way there', () => {
    const { onClose, onOpenRadioSettings } = open();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('radioStatus.openSettings') }));
    expect(onOpenRadioSettings).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('sends a flood or zero-hop advert from the status itself', async () => {
    const { onAdvertise } = open();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('radioStatus.advertFlood') }));
    await waitFor(() => expect(onAdvertise).toHaveBeenCalledWith('flood'));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('radioStatus.advertZeroHop') }));
    await waitFor(() => expect(onAdvertise).toHaveBeenCalledWith('zero_hop'));
  });

  it('does not offer to advert when the radio cannot send', () => {
    open({ health: { radio_connected: false, connection_info: null } as HealthStatus });
    expect(screen.getByRole('button', { name: i18n.t('radioStatus.advertFlood') })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: i18n.t('radioStatus.advertZeroHop') })
    ).toBeDisabled();
  });

  it('hides the update banner when no update is available', () => {
    open();
    expect(screen.queryByText(i18n.t('radioStatus.updateAvailable'))).not.toBeInTheDocument();
  });

  it('shows a clickable update banner that opens updates after close', () => {
    const onOpenUpdates = vi.fn();
    const { onClose } = open({
      updateAvailable: true,
      currentVersion: '4.7.1',
      latestVersion: '4.8.0',
      onOpenUpdates,
    });
    expect(screen.getByText(i18n.t('radioStatus.updateAvailable'))).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('radioStatus.updateAvailableHelp', { current: '4.7.1', latest: '4.8.0' })
      )
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText(i18n.t('radioStatus.updateAvailable')));
    expect(onClose).toHaveBeenCalled();
    expect(onOpenUpdates).toHaveBeenCalled();
    expect(onClose.mock.invocationCallOrder[0]).toBeLessThan(
      onOpenUpdates.mock.invocationCallOrder[0]
    );
  });
});
