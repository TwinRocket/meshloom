# Packet visualizer (`#visualizer`)

The visualizer is a 3D picture of the mesh: each node is a dot, each packet travels along the hops it took. This page explains how it is built.

Read this only when you change the 3D visualizer. Its numbers come from the code listed below. Check them again before you rely on them.

## Layers

| File                                            | Owns                                                                                                                                                                                                                                                          |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `networkGraph/packetNetworkGraph.ts`            | Canonical graph: path resolution, ambiguous-repeater heuristics, advert-path hints, traffic splitting, projection (including the dashed bridges over hidden runs and the sibling collapse). Projection never changes the canonical observations or neighbors. |
| `utils/visualizerUtils.ts`                      | Packet parsing (`parsePacket`), aggregation keys (`generatePacketKey`), traffic analysis, colors.                                                                                                                                                             |
| `components/visualizer/useVisualizerData3D.ts`  | Render nodes/links, the `d3-force-3d` simulation, the observation window, particles, Clear & Reset.                                                                                                                                                           |
| `components/visualizer/useVisualizer3DScene.ts` | Three.js scene: WebGL + CSS2D labels, OrbitControls, persistent buffers updated in place, raycast hover, click-to-pin.                                                                                                                                        |
| `components/visualizer/VisualizerControls.tsx`  | Toolbar placed in the page flow above the canvas, never over it. One transient panel open at a time (Escape or a click outside closes it). Bottom sheet on narrow screens, dropdown from `md` up, in CSS only.                                                |
| `components/visualizer/VisualizerTooltip.tsx`   | Hover/pin details. "Traffic exchanged with" uses the canonical adjacency, so hidden neighbors still appear.                                                                                                                                                   |
| `components/visualizer/shared.ts`               | `GraphNode` / `GraphLink` types, node colors, legend.                                                                                                                                                                                                         |
| `utils/visualizerSettings.ts`                   | Options persisted in localStorage (`meshloom-visualizer-settings`), with their defaults.                                                                                                                                                                      |
| `types/d3-force-3d.d.ts`                        | Minimal type declarations for `d3-force-3d`.                                                                                                                                                                                                                  |

`VisualizerView` mounts **exactly one** `PacketVisualizer3D`. The tab layout and the split layout place the same nodes in two CSS arrangements. A second instance would cost a second WebGL context and a second simulation.

## Pipeline

1. A packet arrives and is deduped on `getRawPacketObservationKey(packet)` (by `observation_id`).
2. `generatePacketKey()` groups repeats as `ad:{pubkey[:12]}`, `gt:{channel}:{sender}:{hash}`, `dm:{src}:{dst}:{hash}`, or `other:{hash}`. The hash is the decoder's `messageHash`, which ignores the path. Malformed packets fall back to a hash of the data.
3. Paths for the same key are collected during the observation window (`observationWindowSec`, default 15 s, 1 to 60 s). They are then published together, and the particles of every path animate at the same time.

## Clock used for activity and pruning

- Pruning (`useVisualizerData3D.ts`, tick every 1 s) compares `Date.now() - pruneStaleMinutes` with each node's and link's `lastActivity`. That is the **browser clock** on both sides.
- `lastActivity` comes from `activityAtMs` in `ingestPacketIntoPacketNetwork`: `packet.received_at_ms ?? normalizePacketTimestampMs(packet.timestamp)`.
- `received_at_ms` is client-only. `recordRawPacket` (`stores/rawPacketStore.ts`) stamps it with `Date.now()` when a live WebSocket packet is recorded. Packets seeded by the history replay (`seedRawPacketStore`) do not have it and keep the **server** timestamp, which is intended: they are historical.
- Never date live traffic with `packet.timestamp` before comparing it with `Date.now()`. The server clock may be minutes off (a host without NTP): a server behind by more than the prune delay used to wipe every node within a second. `src/test/clockSkewPrune.test.ts` covers this.

## Ambiguous repeaters

- When only a short hop token is known, the node ID is `?{hop}`. 2- and 3-byte tokens stay separate and are never shortened to their first byte. A node identified as a repeater is drawn blue even when it is ambiguous.
- **Advert-path hints** (`pickLikelyRepeaterByAdvertPath`): the data comes from `GET /api/contacts/repeaters/advert-paths` (backend table `contact_advert_paths`). A candidate wins when its `heard_count` total is at least 2 and at least twice the runner-up's. It sets a `probableIdentity` label and leaves the node ID unchanged.
- **Traffic splitting**: the node is split into `?{hop}:>{nextHop}` only when every group of sources (one group per next hop) is disjoint from the others and has at least `MIN_OBSERVATIONS_TO_SPLIT` (20) unique sources. The last repeater before self is never split. A display name set by an advert-path hint is not overwritten.

## Simulation (`useVisualizerData3D.ts`)

The link force has distance 120. Charge is `chargeStrength` (default −200, slider 50 to 2500), 6× for self, with `distanceMax` 800. A centering force and self-anchoring X/Y/Z forces keep self near the origin. "Let 'em drift" keeps `alphaTarget(0.05)`. "Oooh Big Stretch!" raises the repulsion for a moment, then lets it relax. New nodes are placed at random 80 to 180 units from the origin, and self sits at the origin. There is no "Shuffle layout" button.

## Defaults (`utils/visualizerSettings.ts`)

Ambiguous repeaters (`showAmbiguousPaths`) on. Ambiguous senders/recipients (`showAmbiguousNodes`) off. Advert-path hints on. Sibling collapse on. Traffic splitting on. Drift on. Particle speed 2× (1 to 5). Prune stale nodes on, after 5 min. Auto-orbit off. Controls shown. Packet feed shown.
