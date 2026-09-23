import { forwardRef } from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import './eSlices';

import { MeshTestMap } from '../components/MeshTestMap';
import type { MeshTestObserver } from '../utils/meshTest';

vi.mock('react-leaflet', () => {
  const BaseLayer = ({ children }: { children: React.ReactNode }) => <div>{children}</div>;
  const LayersControlMock = ({ children }: { children: React.ReactNode }) => <div>{children}</div>;
  (LayersControlMock as unknown as { BaseLayer: typeof BaseLayer }).BaseLayer = BaseLayer;
  return {
    MapContainer: ({ children, className }: { children: React.ReactNode; className?: string }) => (
      <div data-testid="map-container" className={className}>
        {children}
      </div>
    ),
    TileLayer: ({ url }: { url: string }) => <div data-testid="tile-layer" data-url={url} />,
    CircleMarker: forwardRef<
      HTMLDivElement,
      {
        children?: React.ReactNode;
        radius?: number;
        pathOptions?: { fillColor?: string };
      }
    >(({ children, radius, pathOptions }, ref) => (
      <div ref={ref} data-radius={radius} data-fill-color={pathOptions?.fillColor}>
        {children}
      </div>
    )),
    Marker: ({
      children,
      icon,
    }: {
      children?: React.ReactNode;
      icon?: { options?: { className?: string; html?: string } };
    }) => (
      <div
        data-testid="mlc-marker"
        data-class={icon?.options?.className}
        data-html={icon?.options?.html}
      >
        {children}
      </div>
    ),
    Popup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Tooltip: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Polyline: () => <div data-testid="hop-path" />,
    LayersControl: LayersControlMock,
    useMap: () => ({
      setView: vi.fn(),
      fitBounds: vi.fn(),
      invalidateSize: vi.fn(),
      setMaxZoom: vi.fn(),
      setZoom: vi.fn(),
      getZoom: vi.fn(() => 6),
      getContainer: () => document.createElement('div'),
    }),
    useMapEvents: () => null,
  };
});

function observer(overrides: Partial<MeshTestObserver> = {}): MeshTestObserver {
  return {
    key: 'ear',
    name: 'Lyon',
    isMLC: false,
    role: 'repeater',
    hops: 1,
    snr: null,
    rssi: null,
    lat: 43.7,
    lon: 7.26,
    distanceKm: 4,
    path: [],
    ...overrides,
  };
}

describe('MeshTestMap', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        disconnect() {}
        unobserve() {}
      }
    );
  });

  it('uses the same basemap choice as #map', () => {
    localStorage.setItem('meshloom-map-layer', 'dark');
    localStorage.setItem('meshloom-map-layer-chosen', 'true');
    render(
      <MeshTestMap
        origin={{ lat: 43.55, lon: 7.02 }}
        observers={[observer()]}
        selectedKey={null}
        onSelect={() => undefined}
      />
    );
    expect(screen.getByTestId('map-container').className).toContain('basemap-inverted');
    const urls = screen.getAllByTestId('tile-layer').map((el) => el.getAttribute('data-url'));
    expect(urls.some((url) => url?.includes('openstreetmap'))).toBe(true);
    expect(urls.some((url) => url?.includes('arcgisonline'))).toBe(true);
  });

  it('puts the Meshloom mark on a black disc and leaves role dots their own colour', () => {
    render(
      <MeshTestMap
        origin={null}
        observers={[observer(), observer({ key: 'mlc', name: 'Community', isMLC: true })]}
        selectedKey={null}
        onSelect={() => undefined}
      />
    );
    const fills = [...document.querySelectorAll('[data-fill-color]')].map((el) =>
      el.getAttribute('data-fill-color')
    );
    expect(fills).not.toContain('#000');
    const mark = screen.getByTestId('mlc-marker');
    expect(mark.getAttribute('data-class')).toContain('mesh-test-mlc-marker');
    expect(mark.getAttribute('data-html')).toContain('mesh-test-mlc-disc');
    expect(mark.getAttribute('data-html')).toContain('meshloom-mark.svg');
  });

  it('draws a hop path only for the selected ear and hides the others', () => {
    const lyon = observer({
      key: 'lyon',
      name: 'Lyon',
      path: [{ prefix: 'AA11', hopIndex: 0, name: 'Relay', lat: 44.2, lon: 5.8 }],
    });
    const nice = observer({ key: 'nice', name: 'Nice', lat: 43.7, lon: 7.26 });
    const origin = { lat: 43.55, lon: 7.02 };
    const { rerender } = render(
      <MeshTestMap
        origin={origin}
        observers={[lyon, nice]}
        selectedKey={null}
        onSelect={() => undefined}
      />
    );

    expect(screen.getByText('Lyon')).toBeInTheDocument();
    expect(screen.getByText('Nice')).toBeInTheDocument();
    expect(screen.queryByTestId('hop-path')).not.toBeInTheDocument();

    rerender(
      <MeshTestMap
        origin={origin}
        observers={[lyon, nice]}
        selectedKey="lyon"
        onSelect={() => undefined}
      />
    );

    expect(screen.getByText('Lyon')).toBeInTheDocument();
    expect(screen.queryByText('Nice')).not.toBeInTheDocument();
    expect(screen.getByTestId('hop-path')).toBeInTheDocument();
  });
});
