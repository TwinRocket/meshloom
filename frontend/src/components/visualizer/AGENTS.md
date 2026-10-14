# Packet visualizer (`#visualizer`)

Read this only when you change the 3D visualizer. Paths are relative to
`frontend/src/`; numbers and defaults live in the code
(`utils/visualizerSettings.ts`, `useVisualizerData3D.ts`), not here.

## Layers

- `networkGraph/packetNetworkGraph.ts` is the canonical graph: path resolution,
  ambiguous-repeater heuristics, advert-path hints, traffic splitting, projection.
  Projection (hidden runs, sibling collapse) never changes the canonical
  observations or neighbors; the tooltip's "Traffic exchanged with" reads the
  canonical adjacency, so hidden neighbors still appear.
- `components/PacketVisualizer3D.tsx` wires `useVisualizerData3D.ts` (nodes, links,
  `d3-force-3d` simulation, observation window, particles) and
  `useVisualizer3DScene.ts` (Three.js scene, buffers updated in place).
- `VisualizerView` mounts **exactly one** `PacketVisualizer3D`: the tab and split
  layouts place the same nodes in two CSS arrangements. A second instance would
  cost a second WebGL context and a second simulation.
- `VisualizerControls.tsx` sits in the page flow above the canvas, never over it;
  one transient panel open at a time.

## Pipeline

1. Packets are deduped on `getRawPacketObservationKey(packet)` (`observation_id`).
2. `generatePacketKey()` (`utils/visualizerUtils.ts`) groups repeats by the
   decoder's `messageHash`, which ignores the path.
3. Paths for the same key are collected during the observation window, then
   published together so their particles animate at the same time.

## Clock

Pruning compares the browser clock on both sides: `lastActivity` comes from
`packet.received_at_ms`, never from the server `packet.timestamp` for live
traffic. See `frontend/AGENTS.md`, "Clocks".

## Ambiguous repeaters

- With only a short hop token, the node ID is `?{hop}`. 2- and 3-byte tokens stay
  separate and are never shortened to their first byte.
- Advert-path hints (`pickLikelyRepeaterByAdvertPath`) set a `probableIdentity`
  label and leave the node ID unchanged.
- Traffic splitting (`?{hop}:>{nextHop}`) needs disjoint source groups, each with
  at least `MIN_OBSERVATIONS_TO_SPLIT` unique sources (`utils/visualizerUtils.ts`).
  The last repeater before self is never split, and a name set by an advert-path
  hint is not overwritten.
