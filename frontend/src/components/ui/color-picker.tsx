import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

import { getContrastTextColor } from '../../utils/localLabel';

/** HSL is what a person adjusts; hex is what everything else stores. */
function hslToHex(hue: number, saturation: number, lightness: number): string {
  const s = saturation / 100;
  const l = lightness / 100;
  const k = (n: number) => (n + hue / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const channel = (n: number) =>
    Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))));
  const hex = (n: number) => channel(n).toString(16).padStart(2, '0');
  return `#${hex(0)}${hex(8)}${hex(4)}`;
}

function hexToHsl(hex: string): { hue: number; saturation: number; lightness: number } | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return null;
  const value = match[1];
  const r = parseInt(value.slice(0, 2), 16) / 255;
  const g = parseInt(value.slice(2, 4), 16) / 255;
  const b = parseInt(value.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  const delta = max - min;
  if (delta === 0) return { hue: 0, saturation: 0, lightness: Math.round(lightness * 100) };
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  let hue: number;
  if (max === r) hue = ((g - b) / delta) % 6;
  else if (max === g) hue = (b - r) / delta + 2;
  else hue = (r - g) / delta + 4;
  hue = Math.round(hue * 60);
  return {
    hue: hue < 0 ? hue + 360 : hue,
    saturation: Math.round(saturation * 100),
    lightness: Math.round(lightness * 100),
  };
}

export function normalizeHex(input: string): string | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(input.trim());
  return match ? `#${match[1].toLowerCase()}` : null;
}

const HUE_GRADIENT =
  'linear-gradient(to right, #ff0000, #ffff00, #00ff00, #00ffff, #0000ff, #ff00ff, #ff0000)';

/**
 * A colour picker that offers ready-made answers before asking for a new one.
 *
 * The browser's own control is a small square that opens the operating system's
 * picker, which looks like nothing else here and gives a person a colour wheel
 * when what they wanted was "a different one from the other server". The presets
 * answer that in a click; the sliders and the hex field are there for anyone who
 * has an exact colour in mind.
 */
export function ColorPicker({
  value,
  onChange,
  presets,
  label,
  presetsLabel,
  customLabel,
  hueLabel,
  lightnessLabel,
  hexLabel,
}: {
  value: string;
  onChange: (color: string) => void;
  presets: readonly string[];
  label: string;
  presetsLabel: string;
  customLabel: string;
  hueLabel: string;
  lightnessLabel: string;
  hexLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [hexDraft, setHexDraft] = useState(value);
  const containerRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => setHexDraft(value), [value]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const hsl = useMemo(
    () => hexToHsl(value) ?? { hue: 214, saturation: 88, lightness: 20 },
    [value]
  );
  const setHsl = useCallback(
    (change: Partial<typeof hsl>) => {
      const next = { ...hsl, ...change };
      onChange(hslToHex(next.hue, Math.max(next.saturation, 35), next.lightness));
    },
    [hsl, onChange]
  );

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={label}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        style={{ backgroundColor: value, color: getContrastTextColor(value) }}
        className="flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium ring-1 ring-inset ring-black/20 transition hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="font-mono uppercase">{value}</span>
      </button>

      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label={label}
          className="absolute left-0 top-full z-50 mt-2 w-64 space-y-3 rounded-lg border border-border bg-popover p-3 shadow-lg"
        >
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">{presetsLabel}</p>
            <div role="radiogroup" aria-label={presetsLabel} className="grid grid-cols-8 gap-1.5">
              {presets.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  role="radio"
                  aria-checked={value.toLowerCase() === preset.toLowerCase()}
                  aria-label={preset}
                  onClick={() => onChange(preset)}
                  style={{ backgroundColor: preset }}
                  className={`aspect-square rounded-full transition ${
                    value.toLowerCase() === preset.toLowerCase()
                      ? 'ring-2 ring-foreground ring-offset-2 ring-offset-popover'
                      : 'ring-1 ring-black/20 hover:scale-110'
                  }`}
                />
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">{customLabel}</p>
            <input
              type="range"
              min={0}
              max={359}
              value={hsl.hue}
              onChange={(e) => setHsl({ hue: Number(e.target.value) })}
              aria-label={hueLabel}
              className="color-slider h-3 w-full cursor-pointer appearance-none rounded-full"
              style={{ background: HUE_GRADIENT }}
            />
            <input
              type="range"
              min={12}
              max={60}
              value={Math.min(60, Math.max(12, hsl.lightness))}
              onChange={(e) => setHsl({ lightness: Number(e.target.value) })}
              aria-label={lightnessLabel}
              className="color-slider h-3 w-full cursor-pointer appearance-none rounded-full"
              style={{
                background: `linear-gradient(to right, ${hslToHex(hsl.hue, 70, 12)}, ${hslToHex(hsl.hue, 70, 60)})`,
              }}
            />
          </div>

          <label className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">{hexLabel}</span>
            <input
              value={hexDraft}
              onChange={(e) => {
                setHexDraft(e.target.value);
                const normalized = normalizeHex(e.target.value);
                if (normalized) onChange(normalized);
              }}
              onBlur={() => setHexDraft(value)}
              spellCheck={false}
              aria-label={hexLabel}
              className="h-8 w-24 rounded-md border border-input bg-background px-2 font-mono text-sm uppercase"
            />
          </label>
        </div>
      )}
    </div>
  );
}
