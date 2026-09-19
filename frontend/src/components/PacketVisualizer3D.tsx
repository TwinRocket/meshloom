import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../api';
import type { Contact, ContactAdvertPathSummary, RadioConfig, RawPacket } from '../types';
import { getVisualizerSettings, saveVisualizerSettings } from '../utils/visualizerSettings';
import { VisualizerControls } from './visualizer/VisualizerControls';
import { VisualizerTooltip } from './visualizer/VisualizerTooltip';
import { useVisualizerData3D } from './visualizer/useVisualizerData3D';
import { useVisualizer3DScene } from './visualizer/useVisualizer3DScene';

interface PacketVisualizer3DProps {
  packets: RawPacket[];
  contacts: Contact[];
  config: RadioConfig | null;
  fullScreen?: boolean;
  onFullScreenChange?: (fullScreen: boolean) => void;
  radioOffline?: boolean;
  directoryEnabled?: boolean;
}

export function PacketVisualizer3D({
  packets,
  contacts,
  config,
  fullScreen,
  onFullScreenChange,
  radioOffline = false,
  directoryEnabled = false,
}: PacketVisualizer3DProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);

  const [savedSettings] = useState(getVisualizerSettings);
  const [showAmbiguousPaths, setShowAmbiguousPaths] = useState(savedSettings.showAmbiguousPaths);
  const [showAmbiguousNodes, setShowAmbiguousNodes] = useState(savedSettings.showAmbiguousNodes);
  const [useAdvertPathHints, setUseAdvertPathHints] = useState(savedSettings.useAdvertPathHints);
  const [collapseLikelyKnownSiblingRepeaters, setCollapseLikelyKnownSiblingRepeaters] = useState(
    savedSettings.collapseLikelyKnownSiblingRepeaters
  );
  const [splitAmbiguousByTraffic, setSplitAmbiguousByTraffic] = useState(
    savedSettings.splitAmbiguousByTraffic
  );
  const [chargeStrength, setChargeStrength] = useState(savedSettings.chargeStrength);
  const [observationWindowSec, setObservationWindowSec] = useState(
    savedSettings.observationWindowSec
  );
  const [letEmDrift, setLetEmDrift] = useState(savedSettings.letEmDrift);
  const [particleSpeedMultiplier, setParticleSpeedMultiplier] = useState(
    savedSettings.particleSpeedMultiplier
  );
  const [showControls, setShowControls] = useState(savedSettings.showControls);
  const [autoOrbit, setAutoOrbit] = useState(savedSettings.autoOrbit);
  const [pruneStaleNodes, setPruneStaleNodes] = useState(savedSettings.pruneStaleNodes);
  const [pruneStaleMinutes, setPruneStaleMinutes] = useState(savedSettings.pruneStaleMinutes);
  const [repeaterAdvertPaths, setRepeaterAdvertPaths] = useState<ContactAdvertPathSummary[]>([]);

  useEffect(() => {
    saveVisualizerSettings({
      ...getVisualizerSettings(),
      showAmbiguousPaths,
      showAmbiguousNodes,
      useAdvertPathHints,
      collapseLikelyKnownSiblingRepeaters,
      splitAmbiguousByTraffic,
      chargeStrength,
      observationWindowSec,
      letEmDrift,
      particleSpeedMultiplier,
      pruneStaleNodes,
      pruneStaleMinutes,
      autoOrbit,
      showControls,
    });
  }, [
    showAmbiguousPaths,
    showAmbiguousNodes,
    useAdvertPathHints,
    collapseLikelyKnownSiblingRepeaters,
    splitAmbiguousByTraffic,
    chargeStrength,
    observationWindowSec,
    letEmDrift,
    particleSpeedMultiplier,
    pruneStaleNodes,
    pruneStaleMinutes,
    autoOrbit,
    showControls,
  ]);

  useEffect(() => {
    let cancelled = false;

    async function loadRepeaterAdvertPaths() {
      try {
        const data = await api.getRepeaterAdvertPaths(10);
        if (!cancelled) {
          setRepeaterAdvertPaths(data);
        }
      } catch (error) {
        if (!cancelled) {
          console.debug('Failed to load repeater advert path hints', error);
          setRepeaterAdvertPaths([]);
        }
      }
    }

    loadRepeaterAdvertPaths();
    return () => {
      cancelled = true;
    };
  }, [contacts.length]);

  const data = useVisualizerData3D({
    packets,
    contacts,
    config,
    repeaterAdvertPaths,
    showAmbiguousPaths,
    showAmbiguousNodes,
    useAdvertPathHints,
    collapseLikelyKnownSiblingRepeaters,
    splitAmbiguousByTraffic,
    chargeStrength,
    letEmDrift,
    particleSpeedMultiplier,
    observationWindowSec,
    pruneStaleNodes,
    pruneStaleMinutes,
    directoryEnabled,
  });

  const { hoveredNodeId, pinnedNodeId } = useVisualizer3DScene({
    containerRef,
    data,
    autoOrbit,
  });

  const tooltipNodeId = pinnedNodeId ?? hoveredNodeId;

  // The toolbar sits above the canvas in normal flow, so it covers no part of the
  // graph. `containerRef` stays the sole Three.js mount point and the element its
  // ResizeObserver measures.
  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden bg-background">
      <VisualizerControls
        showControls={showControls}
        setShowControls={setShowControls}
        fullScreen={fullScreen}
        onFullScreenChange={onFullScreenChange}
        showAmbiguousPaths={showAmbiguousPaths}
        setShowAmbiguousPaths={setShowAmbiguousPaths}
        showAmbiguousNodes={showAmbiguousNodes}
        setShowAmbiguousNodes={setShowAmbiguousNodes}
        useAdvertPathHints={useAdvertPathHints}
        setUseAdvertPathHints={setUseAdvertPathHints}
        collapseLikelyKnownSiblingRepeaters={collapseLikelyKnownSiblingRepeaters}
        setCollapseLikelyKnownSiblingRepeaters={setCollapseLikelyKnownSiblingRepeaters}
        splitAmbiguousByTraffic={splitAmbiguousByTraffic}
        setSplitAmbiguousByTraffic={setSplitAmbiguousByTraffic}
        observationWindowSec={observationWindowSec}
        setObservationWindowSec={setObservationWindowSec}
        pruneStaleNodes={pruneStaleNodes}
        setPruneStaleNodes={setPruneStaleNodes}
        pruneStaleMinutes={pruneStaleMinutes}
        setPruneStaleMinutes={setPruneStaleMinutes}
        letEmDrift={letEmDrift}
        setLetEmDrift={setLetEmDrift}
        autoOrbit={autoOrbit}
        setAutoOrbit={setAutoOrbit}
        chargeStrength={chargeStrength}
        setChargeStrength={setChargeStrength}
        particleSpeedMultiplier={particleSpeedMultiplier}
        setParticleSpeedMultiplier={setParticleSpeedMultiplier}
        nodeCount={data.stats.nodes}
        linkCount={data.stats.links}
        onExpandContract={data.expandContract}
        onClearAndReset={data.clearAndReset}
      />

      {/* In flow under the toolbar rather than floating over the canvas: the nodes
          move, so any overlay position eventually lands on a label. The graph is
          fed only by live WebSocket packets — stored packets never reach it — and
          stale nodes are pruned on a short window; both are easy to mistake for a
          broken view. */}
      {data.stats.nodes <= 1 && (
        <div className="shrink-0 border-b border-border bg-background px-4 py-2 text-center text-sm text-muted-foreground">
          <p>{radioOffline ? t('visualizer.emptyOffline') : t('visualizer.emptyWaiting')}</p>
          <p className="mt-0.5 text-xs">
            {radioOffline ? t('visualizer.emptyOfflineHint') : t('visualizer.emptyLiveOnly')}
            {!radioOffline &&
              pruneStaleNodes &&
              ' ' + t('visualizer.emptyPruneHint', { count: pruneStaleMinutes })}
          </p>
        </div>
      )}
      <div
        ref={containerRef}
        className="relative min-h-0 flex-1"
        role="img"
        aria-label={t('visualizer.ariaLabel')}
      >
        <VisualizerTooltip
          activeNodeId={tooltipNodeId}
          canonicalNodes={data.canonicalNodes}
          canonicalNeighborIds={data.canonicalNeighborIds}
          renderedNodeIds={data.renderedNodeIds}
          communityNames={data.communityNames}
        />
      </div>
    </div>
  );
}
