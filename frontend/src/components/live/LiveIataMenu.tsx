import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';

import { Checkbox } from '../ui/checkbox';
import type { LiveIataSelection } from '../../utils/liveIataPreference';

interface LiveIataMenuProps {
  /** Codes offered, already sorted (the radio's first). */
  options: string[];
  /** `null` = every region. */
  selection: LiveIataSelection;
  radioIata?: string;
  onChange: (selection: LiveIataSelection) => void;
}

const LABEL_MAX_CODES = 3;

/** Region picker for Live: one checkbox per IATA plus "All regions". */
export function LiveIataMenu({ options, selection, radioIata, onChange }: LiveIataMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const radio = radioIata?.trim().toUpperCase() ?? '';

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    // Focus moves into the panel, on the first box, when it opens.
    panelRef.current?.querySelector<HTMLElement>('button[role="checkbox"], input')?.focus();
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const all = selection === null;
  const chosen = new Set(selection ?? []);
  const label = all
    ? t('live.iataAll')
    : chosen.size <= LABEL_MAX_CODES
      ? [...chosen].sort().join(', ')
      : t('live.iataSelected', { count: chosen.size });

  const toggleAll = (checked: boolean) => {
    if (checked) onChange(null);
    else if (radio) onChange([radio]);
  };

  const toggleCode = (code: string, checked: boolean) => {
    const next = new Set(chosen);
    if (checked) next.add(code);
    else next.delete(code);
    // Unticking the last region shows every region rather than nothing.
    onChange(next.size ? [...next].sort() : null);
  };

  return (
    <div ref={rootRef} className="relative">
      {/* Disclosure: the visible text ("Regions" + the selection) is the name. */}
      <button
        ref={buttonRef}
        type="button"
        className="flex items-center gap-1 rounded border border-border bg-background px-1.5 py-0.5 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={t('live.iataHelp')}
      >
        <span className="text-muted-foreground">{t('live.iataFilter')}</span>
        <span data-testid="live-iata-label">{label}</span>
        <ChevronDown className="h-3 w-3" aria-hidden="true" />
      </button>
      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="group"
          aria-label={t('live.iataMenu')}
          className="absolute left-0 z-50 mt-1 max-h-80 min-w-48 overflow-y-auto rounded-md border border-border bg-background p-2 shadow-md"
        >
          <p className="px-1 pb-2 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
            {t('live.iataMenu')}
          </p>
          <div className="space-y-1">
            <label
              htmlFor="live-iata-all"
              className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm"
            >
              <Checkbox
                id="live-iata-all"
                checked={all}
                onCheckedChange={(checked) => toggleAll(checked === true)}
              />
              <span>{t('live.iataAll')}</span>
            </label>
            {options.map((code) => {
              const id = `live-iata-${code}`;
              return (
                <label
                  key={code}
                  htmlFor={id}
                  className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm"
                >
                  <Checkbox
                    id={id}
                    checked={chosen.has(code)}
                    onCheckedChange={(checked) => toggleCode(code, checked === true)}
                  />
                  <span className="font-mono">{code}</span>
                  {code === radio && (
                    <span className="text-[0.6875rem] text-muted-foreground">
                      {t('live.iataRadio')}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          <p className="mt-2 max-w-[16rem] px-1 text-[0.6875rem] text-muted-foreground">
            {t('live.iataHelp')}
          </p>
        </div>
      )}
    </div>
  );
}
