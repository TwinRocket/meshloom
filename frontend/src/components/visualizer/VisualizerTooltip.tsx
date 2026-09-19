import { useTranslation } from 'react-i18next';
import type { PacketNetworkNode } from '../../networkGraph/packetNetworkGraph';
import { DirectoryGlobeIcon } from '../messagePath/DirectoryGlobeIcon';
import { formatRelativeTime } from './shared';

function formatActivityReason(
  reason: string,
  t: (key: string, opts?: Record<string, string>) => string
) {
  if (reason.startsWith('Relayed ')) {
    return t('visualizer.reasonRelayed', { label: reason.slice('Relayed '.length) });
  }
  if (reason.endsWith(' source')) {
    return t('visualizer.reasonSource', { label: reason.slice(0, -' source'.length) });
  }
  return reason;
}

interface VisualizerTooltipProps {
  activeNodeId: string | null;
  canonicalNodes: Map<string, PacketNetworkNode>;
  canonicalNeighborIds: Map<string, string[]>;
  renderedNodeIds: Set<string>;
  communityNames?: Map<string, string>;
}

export function VisualizerTooltip({
  activeNodeId,
  canonicalNodes,
  canonicalNeighborIds,
  renderedNodeIds,
  communityNames,
}: VisualizerTooltipProps) {
  const { t } = useTranslation();
  if (!activeNodeId) return null;

  const node = canonicalNodes.get(activeNodeId);
  if (!node) return null;

  const communityName = communityNames?.get(activeNodeId);
  const displayName =
    communityName || node.name || (node.type === 'self' ? t('visualizer.me') : node.id.slice(0, 8));

  const neighborIds = canonicalNeighborIds.get(activeNodeId) ?? [];
  const neighbors = neighborIds
    .map((nid) => {
      const neighbor = canonicalNodes.get(nid);
      if (!neighbor) return null;
      const neighborCommunityName = communityNames?.get(nid);
      const neighborName =
        neighborCommunityName ||
        neighbor.name ||
        (neighbor.type === 'self' ? t('visualizer.me') : neighbor.id.slice(0, 8));
      return {
        id: nid,
        name: neighborName,
        fromCommunity: Boolean(neighborCommunityName),
        ambiguousNames: neighbor.ambiguousNames,
        hidden: !renderedNodeIds.has(nid),
      };
    })
    .filter((neighbor): neighbor is NonNullable<typeof neighbor> => neighbor !== null);

  return (
    <div className="absolute top-4 right-4 bg-background/90 backdrop-blur-sm rounded-lg p-3 text-xs border border-border z-10 max-w-72 max-h-[calc(100%-2rem)] overflow-y-auto">
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-1.5 font-medium min-w-0">
          {communityName ? <DirectoryGlobeIcon /> : null}
          <span className="truncate">{displayName}</span>
        </div>
        {communityName ? (
          <div className="text-muted-foreground">{t('path.directoryGlobe')}</div>
        ) : null}
        <div className="text-muted-foreground">{t('visualizer.id', { id: node.id })}</div>
        <div className="text-muted-foreground">
          {node.isAmbiguous
            ? t('visualizer.typeAmbiguous', {
                type: t(`visualizer.nodeType.${node.type}`, { defaultValue: node.type }),
              })
            : t('visualizer.type', {
                type: t(`visualizer.nodeType.${node.type}`, { defaultValue: node.type }),
              })}
        </div>
        {node.probableIdentity && (
          <div className="text-muted-foreground">
            {t('visualizer.probably', { name: node.probableIdentity })}
          </div>
        )}
        {node.ambiguousNames && node.ambiguousNames.length > 0 && (
          <div className="text-muted-foreground">
            {node.probableIdentity
              ? t('visualizer.otherPossible', { names: node.ambiguousNames.join(', ') })
              : t('visualizer.possible', { names: node.ambiguousNames.join(', ') })}
          </div>
        )}
        {node.type !== 'self' && (
          <div className="text-muted-foreground border-t border-border pt-1 mt-1">
            <div>{t('visualizer.lastActive', { time: formatRelativeTime(node.lastActivity) })}</div>
            {node.lastActivityReason && (
              <div>
                {t('visualizer.reason', {
                  reason: formatActivityReason(node.lastActivityReason, t),
                })}
              </div>
            )}
          </div>
        )}
        {neighbors.length > 0 && (
          <div className="text-muted-foreground border-t border-border pt-1 mt-1">
            <div className="mb-0.5">{t('visualizer.trafficWith')}</div>
            <ul className="pl-3 flex flex-col gap-0.5">
              {neighbors.map((neighbor) => (
                <li key={neighbor.id} className="flex items-center gap-1 min-w-0">
                  {neighbor.fromCommunity ? <DirectoryGlobeIcon /> : null}
                  <span className="truncate">{neighbor.name}</span>
                  {neighbor.hidden && (
                    <span className="text-muted-foreground/60 shrink-0">
                      {' '}
                      {t('visualizer.hidden')}
                    </span>
                  )}
                  {neighbor.ambiguousNames && neighbor.ambiguousNames.length > 0 && (
                    <span className="text-muted-foreground/60">
                      {' '}
                      ({neighbor.ambiguousNames.join(', ')})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
