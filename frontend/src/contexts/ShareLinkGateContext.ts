import { createContext, useContext } from 'react';

/**
 * Whether a service link anchor may warm its meta (`GET /api/share/{id}/meta`) on
 * hover or focus. Outside a message body every link may (the T21 hint); a message
 * carrying a shared image says no for that id until the image may load (tapped, or
 * auto-loaded by the preview policy), so nothing leaves before the tap.
 */
export type ShareLinkGate = (id: string) => boolean;

const ALLOW_ALL: ShareLinkGate = () => true;

export const ShareLinkGateContext = createContext<ShareLinkGate>(ALLOW_ALL);

export function useShareLinkGate(): ShareLinkGate {
  return useContext(ShareLinkGateContext);
}
