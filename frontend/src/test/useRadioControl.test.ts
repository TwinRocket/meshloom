import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));

import { api } from '../api';
import i18n from '../i18n';
import { useRadioControl } from '../hooks/useRadioControl';
import type { RadioConfig } from '../types';

const CONFIG: RadioConfig = {
  public_key: 'aa'.repeat(32),
  name: 'Node',
  lat: 0,
  lon: 0,
  tx_power: 17,
  max_tx_power: 22,
  radio: { freq: 433, bw: 250, sf: 11, cr: 5 },
  path_hash_mode: 0,
  path_hash_mode_supported: false,
};

describe('useRadioControl.handleSaveConfig', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.values(mocks.toast).forEach((fn) => fn.mockReset());
    vi.spyOn(api, 'getRadioConfig').mockResolvedValue(CONFIG);
  });

  it('warns when the radio parameters were written without client repeat', async () => {
    vi.spyOn(api, 'updateRadioConfig').mockResolvedValue({
      ...CONFIG,
      warnings: ['client_repeat_not_allowed'],
    });
    const { result } = renderHook(() => useRadioControl());
    await act(async () => {
      await result.current.handleSaveConfig({ radio: CONFIG.radio });
    });
    expect(mocks.toast.warning).toHaveBeenCalledWith(i18n.t('settings.radio.clientRepeatDropped'));
    expect(i18n.t('settings.radio.clientRepeatDropped')).not.toBe(
      'settings.radio.clientRepeatDropped'
    );
  });

  it('stays quiet without warnings', async () => {
    vi.spyOn(api, 'updateRadioConfig').mockResolvedValue(CONFIG);
    const { result } = renderHook(() => useRadioControl());
    await act(async () => {
      await result.current.handleSaveConfig({ name: 'Other' });
    });
    expect(mocks.toast.warning).not.toHaveBeenCalled();
  });
});
