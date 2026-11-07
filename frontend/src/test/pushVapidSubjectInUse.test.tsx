import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { PushPreferences } from '../types';

const state = vi.hoisted(() => ({ preferences: null as PushPreferences | null }));
vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => ({
    isSupported: false,
    allSubscriptions: [],
    preferences: state.preferences,
    loading: false,
    subscribe: vi.fn(),
    currentSubscriptionId: null,
    patchPreferences: vi.fn(),
    deleteSubscription: vi.fn(),
    testPush: vi.fn(),
    refreshSubscriptions: vi.fn(),
  }),
}));

import { PushDevicesBlock } from '../components/settings/notifications/PushDevicesBlock';
import i18n from '../i18n';

function prefs(partial: Partial<PushPreferences>): PushPreferences {
  return {
    defaults: {} as PushPreferences['defaults'],
    overrides: {},
    vapid_subject: '',
    vapid_subject_effective: 'mailto:noreply@meshcore.local',
    vapid_subject_source: 'default',
    ...partial,
  };
}

describe('PushDevicesBlock, VAPID subject in use (MSHL-446)', () => {
  it('names the Meshloom Remote address when the field is empty and Remote is on', async () => {
    await i18n.changeLanguage('en');
    state.preferences = prefs({
      vapid_subject_effective: 'https://k3xwq2m7ta.meshloom.link',
      vapid_subject_source: 'remote',
    });
    render(<PushDevicesBlock appSettings={null} />);
    const line = screen.getByTestId('vapid-subject-in-use');
    expect(line).toHaveAttribute('data-source', 'remote');
    expect(line).toHaveTextContent('https://k3xwq2m7ta.meshloom.link');
    expect(line).toHaveTextContent('Meshloom Remote address');
  });

  it('says in French that the built-in address is refused by iPhones', async () => {
    await i18n.changeLanguage('fr');
    state.preferences = prefs({});
    render(<PushDevicesBlock appSettings={null} />);
    const line = screen.getByTestId('vapid-subject-in-use');
    expect(line).toHaveTextContent('mailto:noreply@meshcore.local');
    expect(line).toHaveTextContent('adresse intégrée');
    await i18n.changeLanguage('en');
  });

  it('shows the typed contact as the one in use', async () => {
    state.preferences = prefs({
      vapid_subject: 'mailto:me@example.org',
      vapid_subject_effective: 'mailto:me@example.org',
      vapid_subject_source: 'stored',
    });
    render(<PushDevicesBlock appSettings={null} />);
    expect(screen.getByTestId('vapid-subject-in-use')).toHaveTextContent('contact typed above');
  });
});
