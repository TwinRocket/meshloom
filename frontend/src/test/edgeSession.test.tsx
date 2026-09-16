import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EdgeSessionExpiredDialog } from '../components/EdgeSessionExpiredDialog';
import {
  edgeSessionLost,
  isEdgeRedirect,
  onEdgeSessionExpired,
  reportEdgeSessionExpired,
  resetEdgeSessionReport,
} from '../utils/edgeSession';

function response(overrides: Partial<Response>): Response {
  return { redirected: false, url: window.location.href, type: 'basic', ...overrides } as Response;
}

describe('edge session detection', () => {
  beforeEach(() => resetEdgeSessionReport());
  afterEach(() => vi.unstubAllGlobals());

  it('treats a response from another origin as the proxy login page', () => {
    expect(
      isEdgeRedirect(response({ redirected: true, url: 'https://team.cloudflareaccess.com/login' }))
    ).toBe(true);
  });

  it('leaves a redirect within the app alone', () => {
    // FastAPI redirects a missing trailing slash. That is not a logout.
    expect(
      isEdgeRedirect(response({ redirected: true, url: `${window.location.origin}/api/health/` }))
    ).toBe(false);
    expect(isEdgeRedirect(response({}))).toBe(false);
  });

  it('reads an opaque redirect from the probe as a lost session', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ type: 'opaqueredirect', status: 0 } as Response)
    );
    await expect(edgeSessionLost()).resolves.toBe(true);
  });

  it('does not call an unreachable server a lost session', async () => {
    // A refused request and a server that is down throw identically; only the
    // probe can tell them apart, and silence is not an answer.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(edgeSessionLost()).resolves.toBe(false);
  });

  it('tells listeners once, however many calls fail', () => {
    const listener = vi.fn();
    onEdgeSessionExpired(listener);
    reportEdgeSessionExpired();
    reportEdgeSessionExpired();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('EdgeSessionExpiredDialog', () => {
  beforeEach(() => resetEdgeSessionReport());
  afterEach(() => vi.unstubAllGlobals());

  it('stays out of the way until the session is gone', () => {
    render(<EdgeSessionExpiredDialog />);
    expect(screen.queryByTestId('edge-session-expired-dialog')).toBeNull();
  });

  it('appears when the session is reported gone', async () => {
    render(<EdgeSessionExpiredDialog />);
    reportEdgeSessionExpired();
    await waitFor(() => expect(screen.getByTestId('edge-session-expired-dialog')).toBeTruthy());
    expect(screen.getByRole('button')).toBeTruthy();
  });
});
