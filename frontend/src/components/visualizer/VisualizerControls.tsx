import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronUp,
  Eye,
  Filter,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  Shapes,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { Checkbox } from '../ui/checkbox';
import { PACKET_LEGEND_ITEMS } from '../../utils/visualizerUtils';
import { DirectoryGlobeIcon } from '../messagePath/DirectoryGlobeIcon';
import { NODE_LEGEND_ITEMS } from './shared';
import { cn } from '@/lib/utils';

const PACKET_DESC_KEYS: Record<string, string> = {
  AD: 'visualizer.packetAd',
  GT: 'visualizer.packetGt',
  DM: 'visualizer.packetDm',
  ACK: 'visualizer.packetAck',
  TR: 'visualizer.packetTr',
  RQ: 'visualizer.packetRq',
  RS: 'visualizer.packetRs',
  '?': 'visualizer.packetOther',
};

const NODE_LABEL_KEYS = [
  'visualizer.legendYou',
  'visualizer.legendRepeater',
  'visualizer.legendNode',
  'visualizer.legendAmbiguous',
] as const;

type PanelId = 'display' | 'filters' | 'layout' | 'legend';

interface VisualizerControlsProps {
  showControls: boolean;
  setShowControls: (value: boolean) => void;
  fullScreen?: boolean;
  onFullScreenChange?: (fullScreen: boolean) => void;
  showAmbiguousPaths: boolean;
  setShowAmbiguousPaths: (value: boolean) => void;
  showAmbiguousNodes: boolean;
  setShowAmbiguousNodes: (value: boolean) => void;
  useAdvertPathHints: boolean;
  setUseAdvertPathHints: (value: boolean) => void;
  collapseLikelyKnownSiblingRepeaters: boolean;
  setCollapseLikelyKnownSiblingRepeaters: (value: boolean) => void;
  splitAmbiguousByTraffic: boolean;
  setSplitAmbiguousByTraffic: (value: boolean) => void;
  observationWindowSec: number;
  setObservationWindowSec: (value: number) => void;
  pruneStaleNodes: boolean;
  setPruneStaleNodes: (value: boolean) => void;
  pruneStaleMinutes: number;
  setPruneStaleMinutes: (value: number) => void;
  letEmDrift: boolean;
  setLetEmDrift: (value: boolean) => void;
  autoOrbit: boolean;
  setAutoOrbit: (value: boolean) => void;
  chargeStrength: number;
  setChargeStrength: (value: number) => void;
  particleSpeedMultiplier: number;
  setParticleSpeedMultiplier: (value: number) => void;
  nodeCount: number;
  linkCount: number;
  onExpandContract: () => void;
  onClearAndReset: () => void;
}

/**
 * Checkbox row. `hint` renders inline rather than in a `title` attribute: these
 * heuristics are undecipherable from the label alone and a hover tooltip is
 * unreachable on touch.
 */
function OptionRow({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer gap-2 py-1.5 md:py-1',
        disabled && 'cursor-not-allowed opacity-50'
      )}
    >
      <Checkbox
        checked={checked}
        disabled={disabled}
        onCheckedChange={(c) => onChange(c === true)}
        className="mt-0.5 shrink-0"
      />
      <span className="flex flex-col gap-0.5">
        <span>{label}</span>
        {hint && (
          <span className="text-[0.6875rem] leading-snug text-muted-foreground">{hint}</span>
        )}
      </span>
    </label>
  );
}

function NumberRow({
  id,
  label,
  unit,
  value,
  min,
  max,
  onCommit,
}: {
  id: string;
  label: string;
  unit: string;
  value: number;
  min: number;
  max: number;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const clamp = (n: number) => Math.max(min, Math.min(max, n));
  return (
    <div className="flex items-center gap-2 py-1.5 md:py-1">
      <label htmlFor={id} className="flex-1 text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={min}
        max={max}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          const parsed = Number.parseInt(e.target.value, 10);
          if (!Number.isNaN(parsed)) onCommit(clamp(parsed));
        }}
        onBlur={() => {
          const parsed = Number.parseInt(draft, 10);
          const next = Number.isNaN(parsed) ? value : clamp(parsed);
          setDraft(String(next));
          if (next !== value) onCommit(next);
        }}
        className="w-16 rounded border border-border bg-background px-2 py-1 text-center text-xs md:py-0.5"
      />
      <span className="w-7 text-muted-foreground">{unit}</span>
    </div>
  );
}

function SliderRow({
  id,
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1 py-1.5 md:py-1">
      <label htmlFor={id} className="text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number.parseFloat(e.target.value))}
        className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-border accent-primary"
      />
    </div>
  );
}

export function VisualizerControls({
  showControls,
  setShowControls,
  fullScreen,
  onFullScreenChange,
  showAmbiguousPaths,
  setShowAmbiguousPaths,
  showAmbiguousNodes,
  setShowAmbiguousNodes,
  useAdvertPathHints,
  setUseAdvertPathHints,
  collapseLikelyKnownSiblingRepeaters,
  setCollapseLikelyKnownSiblingRepeaters,
  splitAmbiguousByTraffic,
  setSplitAmbiguousByTraffic,
  observationWindowSec,
  setObservationWindowSec,
  pruneStaleNodes,
  setPruneStaleNodes,
  pruneStaleMinutes,
  setPruneStaleMinutes,
  letEmDrift,
  setLetEmDrift,
  autoOrbit,
  setAutoOrbit,
  chargeStrength,
  setChargeStrength,
  particleSpeedMultiplier,
  setParticleSpeedMultiplier,
  nodeCount,
  linkCount,
  onExpandContract,
  onClearAndReset,
}: VisualizerControlsProps) {
  const { t } = useTranslation();
  const [openPanel, setOpenPanel] = useState<PanelId | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const idPrefix = useId();

  const close = useCallback(() => setOpenPanel(null), []);

  // Escape, or any pointer outside the toolbar and its panel, dismisses. A click
  // on the canvas therefore both closes the panel and moves the camera, which is
  // what someone reaching back for the graph means.
  useEffect(() => {
    if (!openPanel) return;
    const trigger = rootRef.current?.querySelector<HTMLButtonElement>(
      `[data-panel="${openPanel}"]`
    );
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      close();
      trigger?.focus();
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [openPanel, close]);

  if (!showControls) {
    return (
      <div className="absolute left-2 top-2 z-10">
        <button
          type="button"
          onClick={() => setShowControls(true)}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border bg-background/70 text-muted-foreground backdrop-blur-sm transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          title={t('visualizer.showToolbar')}
          aria-label={t('visualizer.showToolbar')}
        >
          <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    );
  }

  const items: { id: PanelId; label: string; icon: ReactNode }[] = [
    { id: 'display', label: t('visualizer.groupDisplay'), icon: <Eye className="h-3.5 w-3.5" /> },
    {
      id: 'filters',
      label: t('visualizer.groupFilters'),
      icon: <Filter className="h-3.5 w-3.5" />,
    },
    {
      id: 'layout',
      label: t('visualizer.groupLayout'),
      icon: <SlidersHorizontal className="h-3.5 w-3.5" />,
    },
    { id: 'legend', label: t('visualizer.groupLegend'), icon: <Shapes className="h-3.5 w-3.5" /> },
  ];
  const openLabel = items.find((item) => item.id === openPanel)?.label;

  return (
    <div ref={rootRef} className="relative z-20 shrink-0 border-b border-border bg-background">
      <div className="flex items-center gap-0.5 px-1.5 py-1">
        {items.map(({ id, label, icon }) => (
          <button
            key={id}
            type="button"
            data-panel={id}
            onClick={() => setOpenPanel((prev) => (prev === id ? null : id))}
            aria-expanded={openPanel === id}
            aria-controls={`${idPrefix}-panel`}
            aria-label={label}
            className={cn(
              'inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors',
              'hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              openPanel === id && 'bg-accent text-foreground'
            )}
          >
            <span aria-hidden="true">{icon}</span>
            <span className="hidden lg:inline" aria-hidden="true">
              {label}
            </span>
          </button>
        ))}

        <button
          type="button"
          onClick={onClearAndReset}
          className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          title={t('visualizer.clearResetTitle')}
          aria-label={t('visualizer.clearReset')}
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="hidden lg:inline" aria-hidden="true">
            {t('visualizer.resetGraph')}
          </span>
        </button>

        <div className="ml-auto flex min-w-0 items-center gap-1 pl-2">
          <span className="truncate whitespace-nowrap text-[0.6875rem] tabular-nums text-muted-foreground">
            {t('visualizer.nodesShort', { count: nodeCount })}
            {' · '}
            {t('visualizer.linksShort', { count: linkCount })}
          </span>
          {onFullScreenChange && (
            <button
              type="button"
              onClick={() => onFullScreenChange(!fullScreen)}
              className="hidden h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:inline-flex"
              title={t('visualizer.showPacketFeedTitle')}
              aria-label={t('visualizer.showPacketFeed')}
              aria-pressed={!fullScreen}
            >
              {fullScreen ? (
                <PanelRightOpen className="h-4 w-4" aria-hidden="true" />
              ) : (
                <PanelRightClose className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              close();
              setShowControls(false);
            }}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            title={t('visualizer.hideToolbar')}
            aria-label={t('visualizer.hideToolbar')}
          >
            <ChevronUp className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      {openPanel && (
        <div
          ref={panelRef}
          id={`${idPrefix}-panel`}
          role="group"
          aria-label={openLabel}
          className={cn(
            // Narrow: bottom sheet, thumb-reachable. Wide: dropped under its trigger.
            'fixed inset-x-0 bottom-0 z-30 max-h-[70vh] overflow-y-auto rounded-t-xl border-t border-border bg-popover p-4 text-xs shadow-xl',
            'md:absolute md:inset-x-auto md:bottom-auto md:left-1.5 md:top-full md:mt-1 md:max-h-[28rem] md:w-80 md:rounded-lg md:border md:p-3 md:shadow-lg'
          )}
        >
          <div className="mb-2 flex items-center justify-between md:hidden">
            <span className="text-sm font-medium">{openLabel}</span>
            <button
              type="button"
              onClick={close}
              className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
              aria-label={t('visualizer.closePanel')}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          {openPanel === 'display' && (
            <div className="flex flex-col">
              <OptionRow
                checked={showAmbiguousPaths}
                onChange={setShowAmbiguousPaths}
                label={t('visualizer.showAmbiguousRepeaters')}
                hint={t('visualizer.showAmbiguousRepeatersTitle')}
              />
              <OptionRow
                checked={showAmbiguousNodes}
                onChange={setShowAmbiguousNodes}
                label={t('visualizer.showAmbiguousNodes')}
                hint={t('visualizer.showAmbiguousNodesTitle')}
              />
            </div>
          )}

          {openPanel === 'filters' && (
            <div className="flex flex-col">
              <OptionRow
                checked={pruneStaleNodes}
                onChange={setPruneStaleNodes}
                label={t('visualizer.onlyRecent')}
              />
              {pruneStaleNodes && (
                <div className="pl-6">
                  <NumberRow
                    id={`${idPrefix}-prune`}
                    label={t('visualizer.window')}
                    unit={t('visualizer.min')}
                    value={pruneStaleMinutes}
                    min={1}
                    max={60}
                    onCommit={setPruneStaleMinutes}
                  />
                </div>
              )}
              <NumberRow
                id={`${idPrefix}-ack`}
                label={t('visualizer.ackWindow')}
                unit={t('visualizer.sec')}
                value={observationWindowSec}
                min={1}
                max={60}
                onCommit={setObservationWindowSec}
              />

              <div className="mt-2 border-t border-border pt-2">
                <div className="mb-1 text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                  {t('visualizer.advanced')}
                </div>
                {!showAmbiguousPaths && (
                  <p className="mb-1 text-[0.6875rem] leading-snug text-muted-foreground">
                    {t('visualizer.advancedNeedsAmbiguous')}
                  </p>
                )}
                <OptionRow
                  checked={useAdvertPathHints}
                  onChange={setUseAdvertPathHints}
                  disabled={!showAmbiguousPaths}
                  label={t('visualizer.advertPathHints')}
                  hint={t('visualizer.advertPathHintsTitle')}
                />
                <OptionRow
                  checked={collapseLikelyKnownSiblingRepeaters}
                  onChange={setCollapseLikelyKnownSiblingRepeaters}
                  disabled={!showAmbiguousPaths || !useAdvertPathHints}
                  label={t('visualizer.collapseSiblings')}
                />
                <OptionRow
                  checked={splitAmbiguousByTraffic}
                  onChange={setSplitAmbiguousByTraffic}
                  disabled={!showAmbiguousPaths}
                  label={t('visualizer.groupByTraffic')}
                />
              </div>
            </div>
          )}

          {openPanel === 'layout' && (
            <div className="flex flex-col">
              <OptionRow
                checked={letEmDrift}
                onChange={setLetEmDrift}
                label={t('visualizer.letEmDrift')}
              />
              <OptionRow
                checked={autoOrbit}
                onChange={setAutoOrbit}
                label={t('visualizer.orbit')}
              />
              <SliderRow
                id={`${idPrefix}-repulsion`}
                label={t('visualizer.repulsion', { value: Math.abs(chargeStrength) })}
                value={Math.abs(chargeStrength)}
                min={50}
                max={2500}
                onChange={(value) => setChargeStrength(-value)}
              />
              <SliderRow
                id={`${idPrefix}-speed`}
                label={t('visualizer.packetSpeed', { value: particleSpeedMultiplier })}
                value={particleSpeedMultiplier}
                min={1}
                max={5}
                step={0.5}
                onChange={setParticleSpeedMultiplier}
              />
              <button
                type="button"
                onClick={onExpandContract}
                title={t('visualizer.bigStretchTitle')}
                className="mt-2 rounded border border-primary/40 bg-primary/10 px-3 py-2 text-xs text-primary transition-colors hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:py-1.5"
              >
                {t('visualizer.bigStretch')}
              </button>
            </div>
          )}

          {openPanel === 'legend' && (
            <div className="flex gap-6">
              <div className="flex flex-col gap-1.5">
                <div className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                  {t('visualizer.packets')}
                </div>
                {PACKET_LEGEND_ITEMS.map((item) => (
                  <div key={item.label} className="flex items-center gap-2">
                    <div
                      className="flex h-5 w-5 items-center justify-center rounded-full text-[0.5rem] font-bold text-white"
                      style={{ backgroundColor: item.color }}
                    >
                      {item.label}
                    </div>
                    <span>{t(PACKET_DESC_KEYS[item.label] ?? 'visualizer.packetOther')}</span>
                  </div>
                ))}
              </div>
              <div className="flex flex-col gap-1.5">
                <div className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                  {t('visualizer.nodes')}
                </div>
                {NODE_LEGEND_ITEMS.map((item, index) => (
                  <div key={item.label} className="flex items-center gap-2">
                    <div
                      className="rounded-full"
                      style={{
                        width: item.size,
                        height: item.size,
                        backgroundColor: item.color,
                      }}
                    />
                    <span>{t(NODE_LABEL_KEYS[index])}</span>
                  </div>
                ))}
                <div className="flex items-center gap-2">
                  <DirectoryGlobeIcon />
                  <span>{t('path.directoryGlobe')}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
