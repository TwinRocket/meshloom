import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import { useOssUpdates } from '../hooks/useOssUpdates';

describe('useOssUpdates', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('loads /api/updates on mount', async () => {
    const payload = {
      current: '1.0.0',
      latest: '1.1.0',
      update_available: true,
      html_url: 'https://example.invalid/release',
    };
    vi.spyOn(api, 'getUpdates').mockResolvedValue(payload);

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current).toEqual(payload);
    });
    expect(api.getUpdates).toHaveBeenCalledTimes(1);
  });
});
