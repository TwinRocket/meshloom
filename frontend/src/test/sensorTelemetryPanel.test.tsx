import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SensorTelemetryPanel } from '../components/SensorTelemetryPanel';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import sliceBEn from '../i18n/locales/slices/b.en.json';
import sliceBFr from '../i18n/locales/slices/b.fr.json';
import sliceFEn from '../i18n/locales/slices/f.en.json';
import sliceFFr from '../i18n/locales/slices/f.fr.json';
import { CONTACT_TYPE_SENSOR, type Contact } from '../types';

i18n.addResourceBundle('en', 'translation', sliceBEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceBFr, true, true);
i18n.addResourceBundle('en', 'translation', sliceFEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceFFr, true, true);

const { requestContactTelemetry, contactTelemetryHistory } = vi.hoisted(() => ({
  requestContactTelemetry: vi.fn(),
  contactTelemetryHistory: vi.fn(),
}));

vi.mock('../api', () => ({
  api: {
    requestContactTelemetry,
    contactTelemetryHistory,
  },
  isAbortError: () => false,
}));

vi.mock('../components/ui/sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

const sensorContact: Contact = {
  public_key: 'ee'.repeat(32),
  name: 'Weather',
  type: CONTACT_TYPE_SENSOR,
  flags: 0,
  direct_path: null,
  direct_path_len: 0,
  direct_path_hash_mode: 0,
  last_advert: null,
  lat: null,
  lon: null,
  last_seen: null,
  on_radio: false,
  favorite: false,
  last_contacted: null,
  last_read_at: null,
  first_seen: null,
};

describe('SensorTelemetryPanel', () => {
  beforeEach(() => {
    requestContactTelemetry.mockReset();
    contactTelemetryHistory.mockReset();
    contactTelemetryHistory.mockResolvedValue([]);
    vi.mocked(toast.error).mockClear();
  });

  it('loads contact telemetry history and requests a live sample', async () => {
    contactTelemetryHistory.mockResolvedValue([
      {
        timestamp: 1_700_000_000,
        data: { lpp_sensors: [{ channel: 1, type_name: 'temperature', value: 21.5 }] },
      },
    ]);
    requestContactTelemetry.mockResolvedValue({
      sensors: [{ channel: 1, type_name: 'temperature', value: 22 }],
      fetched_at: 1_700_000_100,
      telemetry_history: [
        {
          timestamp: 1_700_000_100,
          data: { lpp_sensors: [{ channel: 1, type_name: 'temperature', value: 22 }] },
        },
      ],
    });

    const onToggle = vi.fn(async () => {});
    render(
      <SensorTelemetryPanel
        contact={sensorContact}
        contacts={[sensorContact]}
        trackedTelemetryContacts={[]}
        onToggleTrackedTelemetryContact={onToggle}
      />
    );

    expect(await screen.findByTestId('sensor-telemetry-panel')).toBeInTheDocument();
    await waitFor(() => {
      expect(contactTelemetryHistory).toHaveBeenCalledWith(sensorContact.public_key);
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('contactInfo.request') }));
    await waitFor(() => {
      expect(requestContactTelemetry).toHaveBeenCalledWith(sensorContact.public_key);
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('contactInfo.startTracking') }));
    await waitFor(() => {
      expect(onToggle).toHaveBeenCalledWith(sensorContact.public_key);
    });
  });

  it('shows notFetched when telemetry history is empty', async () => {
    render(
      <SensorTelemetryPanel
        contact={sensorContact}
        contacts={[sensorContact]}
        trackedTelemetryContacts={[]}
      />
    );

    await waitFor(() => {
      expect(contactTelemetryHistory).toHaveBeenCalledWith(sensorContact.public_key);
    });
    expect(screen.getByText(i18n.t('contactInfo.notFetched'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('contactInfo.noSensorData'))).not.toBeInTheDocument();
  });

  it('shows notFetched when history has no lpp_sensors field', async () => {
    contactTelemetryHistory.mockResolvedValue([
      { timestamp: 1_700_000_000, data: {} },
    ]);

    render(
      <SensorTelemetryPanel
        contact={sensorContact}
        contacts={[sensorContact]}
        trackedTelemetryContacts={[]}
      />
    );

    await waitFor(() => {
      expect(contactTelemetryHistory).toHaveBeenCalledWith(sensorContact.public_key);
    });
    expect(screen.getByText(i18n.t('contactInfo.notFetched'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('contactInfo.noSensorData'))).not.toBeInTheDocument();
  });

  it('shows noSensorData when history has an empty lpp_sensors list', async () => {
    contactTelemetryHistory.mockResolvedValue([
      { timestamp: 1_700_000_000, data: { lpp_sensors: [] } },
    ]);

    render(
      <SensorTelemetryPanel
        contact={sensorContact}
        contacts={[sensorContact]}
        trackedTelemetryContacts={[]}
      />
    );

    expect(await screen.findByText(i18n.t('contactInfo.noSensorData'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('contactInfo.notFetched'))).not.toBeInTheDocument();
  });

  it('toasts telemetryFailed when a live telemetry request rejects', async () => {
    requestContactTelemetry.mockRejectedValue('upstream failed');

    render(
      <SensorTelemetryPanel
        contact={sensorContact}
        contacts={[sensorContact]}
        trackedTelemetryContacts={[]}
      />
    );

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('contactInfo.request') }));
    await waitFor(() => {
      expect(requestContactTelemetry).toHaveBeenCalledWith(sensorContact.public_key);
      expect(toast.error).toHaveBeenCalledWith(i18n.t('contactInfo.telemetryFailed'));
    });
  });
});
