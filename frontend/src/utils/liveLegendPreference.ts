export const LIVE_PACKET_LEGEND_OPEN_KEY = 'meshloom-live-packet-legend-open';

export function getSavedLivePacketLegendOpen(): boolean {
  try {
    return localStorage.getItem(LIVE_PACKET_LEGEND_OPEN_KEY) !== 'false';
  } catch {
    return true;
  }
}

export function setSavedLivePacketLegendOpen(open: boolean): void {
  try {
    localStorage.setItem(LIVE_PACKET_LEGEND_OPEN_KEY, open ? 'true' : 'false');
  } catch {
    /* localStorage may be unavailable */
  }
}
