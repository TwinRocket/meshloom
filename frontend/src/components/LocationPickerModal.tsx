import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CircleMarker, MapContainer, TileLayer, useMap, useMapEvents } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import {
  formatPickedCoordinate,
  lookupCommunityIataLocation,
  parseLocationPair,
  resolveBrowserTimezoneLocation,
  resolveLocationPickerOrigin,
  type LocationPickerOrigin,
  type LocationPickerPoint,
} from '../utils/locationPicker';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';

function InvalidateOnSize() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    const sync = () => {
      map.invalidateSize();
    };
    const ro = new ResizeObserver(sync);
    ro.observe(container);
    sync();
    return () => ro.disconnect();
  }, [map]);
  return null;
}

function ClickToPick({ onPick }: { onPick: (point: LocationPickerPoint) => void }) {
  useMapEvents({
    click(event) {
      onPick({ lat: event.latlng.lat, lon: event.latlng.lng });
    },
  });
  return null;
}

function PickerMap({
  origin,
  picked,
  onPick,
}: {
  origin: LocationPickerOrigin;
  picked: LocationPickerPoint | null;
  onPick: (point: LocationPickerPoint) => void;
}) {
  return (
    <MapContainer
      center={[origin.lat, origin.lon]}
      zoom={origin.zoom}
      className="h-full w-full"
      style={{ background: 'hsl(var(--muted))' }}
    >
      <InvalidateOnSize />
      <ClickToPick onPick={onPick} />
      <TileLayer
        attribution={OSM_RASTER_TILE_ATTRIBUTION}
        url={OSM_RASTER_TILE_URL}
        referrerPolicy={OSM_RASTER_REFERRER_POLICY}
      />
      {picked && (
        <CircleMarker
          center={[picked.lat, picked.lon]}
          radius={9}
          pathOptions={{
            color: 'hsl(var(--primary))',
            fillColor: 'hsl(var(--primary))',
            fillOpacity: 0.9,
            weight: 2,
          }}
        />
      )}
    </MapContainer>
  );
}

export function LocationPickerModal({
  open,
  onOpenChange,
  initialLat,
  initialLon,
  onApply,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialLat: string;
  initialLon: string;
  onApply: (lat: number, lon: number) => void;
}) {
  const { t } = useTranslation();
  const [origin, setOrigin] = useState<LocationPickerOrigin | null>(null);
  const [picked, setPicked] = useState<LocationPickerPoint | null>(null);

  useEffect(() => {
    if (!open) return;

    const current = parseLocationPair(initialLat, initialLon);
    if (current) {
      setOrigin(resolveLocationPickerOrigin(current, null));
      setPicked(current);
      return;
    }

    let cancelled = false;
    setOrigin(null);
    setPicked(null);
    void lookupCommunityIataLocation().then((iata) => {
      if (cancelled) return;
      const timezone = resolveBrowserTimezoneLocation();
      const next = resolveLocationPickerOrigin(null, iata, timezone);
      setOrigin(next);
      if (next.source === 'iata' || next.source === 'timezone') {
        setPicked({ lat: next.lat, lon: next.lon });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [open, initialLat, initialLon]);

  const handleApply = useCallback(() => {
    if (!picked) return;
    onApply(picked.lat, picked.lon);
    onOpenChange(false);
  }, [onApply, onOpenChange, picked]);

  const originHint =
    origin?.source === 'iata' && origin.iata
      ? t('settings.radio.pickOnMapCenteredIata', { iata: origin.iata })
      : origin?.source === 'timezone' && origin.city && origin.timeZone
        ? t('settings.radio.pickOnMapCenteredTimezone', {
            city: origin.city,
            timeZone: origin.timeZone,
          })
        : origin?.source === 'current'
          ? t('settings.radio.pickOnMapCenteredCurrent')
          : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('settings.radio.pickOnMapTitle')}</DialogTitle>
          <DialogDescription>{t('settings.radio.pickOnMapHelp')}</DialogDescription>
        </DialogHeader>
        {originHint && <p className="text-xs text-muted-foreground">{originHint}</p>}
        <div
          className="h-[min(60vh,420px)] w-full overflow-hidden rounded-md border border-border"
          role="application"
          aria-label={t('settings.radio.pickOnMapTitle')}
        >
          {origin ? (
            <PickerMap origin={origin} picked={picked} onPick={setPicked} />
          ) : (
            <div className="h-full w-full animate-pulse bg-muted/30" />
          )}
        </div>
        <p className="font-mono text-xs tabular-nums text-muted-foreground">
          {picked
            ? `${formatPickedCoordinate(picked.lat)}, ${formatPickedCoordinate(picked.lon)}`
            : t('settings.radio.pickOnMapNoPoint')}
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t('settings.radio.cancel')}
          </Button>
          <Button type="button" onClick={handleApply} disabled={!picked}>
            {t('settings.radio.pickOnMapSave')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
