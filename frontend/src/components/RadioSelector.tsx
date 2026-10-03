import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown, Radio as RadioIcon } from 'lucide-react';
import { useRadioContext } from '../contexts/RadioContext';
import { cn } from '../lib/utils';
import type { RadioRecord } from '../types';

export function getRadioStatusDotClass(radio?: RadioRecord): string {
  if (!radio) return 'bg-status-disconnected';
  if (!radio.enabled) return 'bg-muted-foreground/40';
  if (radio.is_connected) return 'bg-status-connected';
  if (radio.is_reconnecting) return 'bg-warning animate-pulse';
  return 'bg-status-disconnected';
}

export function getRadioTransportBadge(radio?: RadioRecord): string {
  if (!radio?.transport) return 'TCP';
  return radio.transport.toUpperCase();
}

interface RadioSelectorProps {
  className?: string;
  onOpenSettings?: () => void;
}

export function RadioSelector({ className, onOpenSettings }: RadioSelectorProps) {
  const { t } = useTranslation();
  const { radios, activeRadioId, setActiveRadioId, activeRadio } = useRadioContext();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close dropdown on click outside
  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  // If only 1 radio (or 0) configured, preserve single-radio simplicity (render nothing)
  if (radios.length <= 1) {
    return null;
  }

  const activeName = activeRadio?.name || activeRadioId || 'Primary Radio';
  const activeDotClass = getRadioStatusDotClass(activeRadio);

  return (
    <div ref={containerRef} className={cn('relative inline-flex items-center', className)}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t('radioSelector.selectRadio', 'Select active radio')}
        className={cn(
          'inline-flex h-8 items-center gap-2 rounded-full border border-border/80 bg-background/90 px-2.5 py-1 text-xs font-medium shadow-sm transition-colors',
          'hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
        )}
      >
        <span className={cn('h-2 w-2 shrink-0 rounded-full', activeDotClass)} aria-hidden="true" />
        <span className="max-w-[8rem] truncate font-semibold text-foreground md:max-w-[11rem]">
          {activeName}
        </span>
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150',
            open && 'rotate-180'
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label={t('radioSelector.radiosList', 'Radios')}
          className="absolute right-0 top-full z-50 mt-1.5 min-w-[15rem] overflow-hidden rounded-xl border border-border bg-popover/95 p-1 text-popover-foreground shadow-xl backdrop-blur-md animate-in fade-in-0 zoom-in-95"
        >
          <div className="flex items-center justify-between px-2.5 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider text-muted-foreground">
            <span>{t('radioSelector.title', 'Active Radio')}</span>
            <span className="text-[0.625rem] text-muted-foreground/80">{radios.length}</span>
          </div>

          <div className="max-h-60 space-y-0.5 overflow-y-auto py-0.5">
            {radios.map((radio) => {
              const isSelected = radio.id === activeRadioId;
              const dotClass = getRadioStatusDotClass(radio);
              const transportBadge = getRadioTransportBadge(radio);

              return (
                <button
                  key={radio.id}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => {
                    setActiveRadioId(radio.id);
                    setOpen(false);
                  }}
                  className={cn(
                    'group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition-colors',
                    isSelected
                      ? 'bg-accent font-medium text-accent-foreground'
                      : 'hover:bg-muted/60 text-foreground'
                  )}
                >
                  <span
                    className={cn('h-2 w-2 shrink-0 rounded-full', dotClass)}
                    aria-hidden="true"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate">{radio.name || radio.id}</span>
                      <span className="shrink-0 rounded bg-muted px-1 py-0.2 text-[0.625rem] font-medium text-muted-foreground">
                        {transportBadge}
                      </span>
                    </div>
                    {radio.name && radio.id !== radio.name && (
                      <span className="block truncate text-[0.625rem] text-muted-foreground">
                        {radio.id}
                      </span>
                    )}
                  </div>
                  {isSelected && (
                    <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                  )}
                </button>
              );
            })}
          </div>

          {onOpenSettings && (
            <>
              <div className="my-1 border-t border-border/50" />
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  onOpenSettings();
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <RadioIcon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>{t('radioSelector.manageRadios', 'Manage Radios...')}</span>
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
