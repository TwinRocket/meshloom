import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { UpdateHoldBanner } from '../components/UpdateHoldBanner';
import i18n from '../i18n';
import type { OssUpdateStatus } from '../types';
import {
  UPDATE_HOLD_DISMISSED_KEY,
  resolveHoldReason,
  updateHoldNeedsAttention,
} from '../utils/updateHold';

function status(overrides: Partial<OssUpdateStatus> = {}): OssUpdateStatus {
  return {
    current: '4.18.3',
    latest: '5.0.0',
    update_available: true,
    html_url: null,
    install_kind: 'package',
    apply_supported: true,
    auto_update: true,
    auto_update_held: true,
    job: { state: 'idle', phase: null, percent: null, error: null, started_at: null },
    ...overrides,
  };
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('updateHoldNeedsAttention', () => {
  it('needs auto-update on, a hold, an update and a node that can install', () => {
    expect(updateHoldNeedsAttention(status())).toBe(true);
    expect(updateHoldNeedsAttention(status({ auto_update: false }))).toBe(false);
    expect(updateHoldNeedsAttention(status({ auto_update_held: false }))).toBe(false);
    expect(updateHoldNeedsAttention(status({ auto_update_held: undefined }))).toBe(false);
    expect(updateHoldNeedsAttention(status({ update_available: false }))).toBe(false);
    expect(updateHoldNeedsAttention(status({ apply_supported: false }))).toBe(false);
    expect(updateHoldNeedsAttention(status({ latest: null }))).toBe(false);
    expect(updateHoldNeedsAttention(null)).toBe(false);
  });
});

describe('resolveHoldReason', () => {
  const reason = { fr: 'raison', en: 'reason' };

  it('prefers the current locale, region tags included', () => {
    expect(resolveHoldReason(reason, 'fr', 'local')).toBe('raison');
    expect(resolveHoldReason(reason, 'en', 'local')).toBe('reason');
    expect(resolveHoldReason(reason, 'fr-CA', 'local')).toBe('raison');
  });

  it('falls back to English, then to the local text', () => {
    expect(resolveHoldReason(reason, 'de', 'local')).toBe('reason');
    expect(resolveHoldReason({ fr: ' ', en: 'reason' }, 'fr', 'local')).toBe('reason');
    expect(resolveHoldReason({ fr: '', en: '' }, 'fr', 'local')).toBe('local');
    expect(resolveHoldReason(null, 'fr', 'local')).toBe('local');
    expect(resolveHoldReason(undefined, undefined, 'local')).toBe('local');
  });

  it('never returns an inherited property', () => {
    expect(resolveHoldReason({ fr: '', en: '' }, 'constructor', 'local')).toBe('local');
  });
});

describe('UpdateHoldBanner', () => {
  it('names the version and opens the update settings', () => {
    const onOpenUpdates = vi.fn();
    render(<UpdateHoldBanner updates={status()} onOpenUpdates={onOpenUpdates} />);
    expect(screen.getByTestId('update-hold-banner')).toHaveTextContent(
      i18n.t('updates.holdBanner', { latest: '5.0.0' })
    );
    fireEvent.click(screen.getByRole('button', { name: i18n.t('updates.holdOpenSettings') }));
    expect(onOpenUpdates).toHaveBeenCalled();
  });

  it('stays hidden when auto-update is off', () => {
    render(<UpdateHoldBanner updates={status({ auto_update: false })} onOpenUpdates={vi.fn()} />);
    expect(screen.queryByTestId('update-hold-banner')).toBeNull();
  });

  it('remembers the dismissal for that version only', () => {
    const { unmount } = render(<UpdateHoldBanner updates={status()} onOpenUpdates={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('updates.holdDismiss') }));
    expect(screen.queryByTestId('update-hold-banner')).toBeNull();
    expect(localStorage.getItem(UPDATE_HOLD_DISMISSED_KEY)).toBe('5.0.0');
    unmount();

    const again = render(<UpdateHoldBanner updates={status()} onOpenUpdates={vi.fn()} />);
    expect(screen.queryByTestId('update-hold-banner')).toBeNull();
    again.rerender(
      <UpdateHoldBanner updates={status({ latest: '5.1.0' })} onOpenUpdates={vi.fn()} />
    );
    expect(screen.getByTestId('update-hold-banner')).toBeInTheDocument();
  });

  it('dismisses for the session when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    render(<UpdateHoldBanner updates={status()} onOpenUpdates={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('updates.holdDismiss') }));
    expect(screen.queryByTestId('update-hold-banner')).toBeNull();
  });
});
