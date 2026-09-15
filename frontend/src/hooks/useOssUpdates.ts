import { useEffect, useState } from 'react';

import { api } from '../api';
import type { OssUpdateStatus } from '../types';

/** Backend already caches at 300s; this is just so a long-lived tab notices. */
export const OSS_UPDATE_POLL_MS = 5 * 60 * 1000;

export function useOssUpdates(): OssUpdateStatus | null {
  const [status, setStatus] = useState<OssUpdateStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      if (typeof api.getUpdates !== 'function') return;
      try {
        const data = await api.getUpdates();
        if (!cancelled) setStatus(data);
      } catch {
        // Keep the last successful snapshot; the badge is best-effort.
      }
    };

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, OSS_UPDATE_POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return status;
}
