import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { ObserverReachEntry, ObserverReachMapHop } from '../types';

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="observer-reach-map">{children}</div>
  ),
  TileLayer: () => null,
  CircleMarker: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Popup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  Polyline: () => <div data-testid="observer-reach-path" />,
  useMap: () => ({
    getContainer: () => document.createElement('div'),
    invalidateSize: () => undefined,
    setView: () => undefined,
    fitBounds: () => undefined,
  }),
}));

import { ObserverReachMap } from '../components/ObserverReachMap';

function observerKey(observer: ObserverReachEntry, index: number): string {
  return `${observer.public_key ?? observer.name}-${index}`;
}

const observers: ObserverReachEntry[] = [
  {
    name: 'Lyon',
    hops: 2,
    path: ['ab', 'cd'],
    lat: 45.75,
    lon: 4.85,
  },
  {
    name: 'Paris',
    hops: 0,
    path: [],
    lat: 48.85,
    lon: 2.35,
  },
];

const hops: ObserverReachMapHop[] = [
  { prefix: 'AB', hopIndex: 0, lat: 46.2, lon: 5.1, name: 'Relay-A' },
  { prefix: 'CD', hopIndex: 1, lat: 47.1, lon: 3.8, name: 'Relay-B' },
];

function renderMap(selectedKey: string | null, selectedHops: ObserverReachMapHop[] = []) {
  return render(
    <ObserverReachMap
      observers={observers}
      origin={{ lat: 45.76, lon: 4.83 }}
      selectedKey={selectedKey}
      selectedHops={selectedHops}
      observerKey={observerKey}
      onSelect={() => undefined}
    />
  );
}

describe('ObserverReachMap focus', () => {
  it('shows every observer and hides hops while nothing is expanded', () => {
    renderMap(null);

    expect(screen.getByText('Lyon')).toBeInTheDocument();
    expect(screen.getByText('Paris')).toBeInTheDocument();
    expect(screen.queryByText('AB')).not.toBeInTheDocument();
    expect(screen.queryByText('CD')).not.toBeInTheDocument();
    expect(screen.queryByTestId('observer-reach-path')).not.toBeInTheDocument();
  });

  it('keeps only the expanded observer and its hops', () => {
    renderMap(observerKey(observers[0], 0), hops);

    expect(screen.getByText('Lyon')).toBeInTheDocument();
    expect(screen.getByText('AB')).toBeInTheDocument();
    expect(screen.getByText('CD')).toBeInTheDocument();
    expect(screen.queryByText('Paris')).not.toBeInTheDocument();
    expect(screen.getByTestId('observer-reach-path')).toBeInTheDocument();
  });

  it('restores every observer when the expanded row is closed', () => {
    const view = renderMap(observerKey(observers[0], 0), hops);
    expect(screen.queryByText('Paris')).not.toBeInTheDocument();

    view.rerender(
      <ObserverReachMap
        observers={observers}
        origin={{ lat: 45.76, lon: 4.83 }}
        selectedKey={null}
        selectedHops={[]}
        observerKey={observerKey}
        onSelect={() => undefined}
      />
    );

    expect(screen.getByText('Lyon')).toBeInTheDocument();
    expect(screen.getByText('Paris')).toBeInTheDocument();
    expect(screen.queryByText('AB')).not.toBeInTheDocument();
    expect(screen.queryByTestId('observer-reach-path')).not.toBeInTheDocument();
  });
});
