import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Maximize2, Minimize2 } from 'lucide-react';
import type { Channel, Contact, RawPacket, RadioConfig } from '../types';
import { PacketVisualizer3D } from './PacketVisualizer3D';
import { RawPacketList } from './RawPacketList';
import { RawPacketInspectorDialog } from './RawPacketDetailModal';
import { ToolPaneHeader } from './ToolPaneHeader';
import { Tabs, TabsList, TabsTrigger } from './ui/tabs';
import { cn } from '@/lib/utils';
import { getVisualizerSettings, saveVisualizerSettings } from '../utils/visualizerSettings';
import { useRawPackets } from '../stores/rawPacketStore';

interface VisualizerViewProps {
  /** Leaves this sub-screen for the Tools screen. Phones only. */
  onBackToTools?: () => void;
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
  radioOffline?: boolean;
  directoryEnabled?: boolean;
}

export function VisualizerView({
  onBackToTools,
  contacts,
  channels,
  config,
  radioOffline = false,
  directoryEnabled = false,
}: VisualizerViewProps) {
  const { t } = useTranslation();
  const packets = useRawPackets();
  const [fullScreen, setFullScreen] = useState(() => getVisualizerSettings().hidePacketFeed);
  const [paneFullScreen, setPaneFullScreen] = useState(false);
  const [mobileTab, setMobileTab] = useState('visualizer');
  const [selectedPacket, setSelectedPacket] = useState<RawPacket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Persist packet feed visibility to localStorage
  useEffect(() => {
    const current = getVisualizerSettings();
    if (current.hidePacketFeed !== fullScreen) {
      saveVisualizerSettings({ ...current, hidePacketFeed: fullScreen });
    }
  }, [fullScreen]);

  // Sync state when browser exits fullscreen (Escape, F11, etc.)
  useEffect(() => {
    const handler = () => {
      if (!document.fullscreenElement) setPaneFullScreen(false);
    };
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  const toggleFullScreen = useCallback(() => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen();
      setPaneFullScreen(true);
    } else {
      document.exitFullscreen();
      // State synced via fullscreenchange handler
    }
  }, []);

  return (
    <div ref={containerRef} className="flex flex-col h-full bg-background">
      {/* Header */}
      <ToolPaneHeader
        title={paneFullScreen ? t('visualizer.titleFullscreen') : t('visualizer.title')}
        onBack={onBackToTools}
        actions={
          <button
            className="hidden md:inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={toggleFullScreen}
            title={paneFullScreen ? t('visualizer.exitFullscreen') : t('visualizer.fullscreen')}
            aria-label={
              paneFullScreen ? t('visualizer.exitFullscreen') : t('visualizer.enterFullscreen')
            }
          >
            {paneFullScreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
          </button>
        }
      />

      {/* One graph, one feed, two layouts. Rendering a second PacketVisualizer3D for
          the narrow layout costs a second WebGL context, scene and force simulation
          that nobody ever sees, so the panes are placed with CSS instead.
          The split starts at lg, not md: at 768 the sidebar plus a fixed-width feed
          left the graph about 30px wide. Below lg, tablets get the tab layout and a
          full-width graph. The feed is then sized as a share of what is left rather
          than a fixed width, so the graph never gets squeezed as the window shrinks. */}
      <Tabs
        value={mobileTab}
        onValueChange={setMobileTab}
        className="flex flex-1 flex-col overflow-hidden"
      >
        <TabsList className="mx-4 mt-2 grid grid-cols-2 lg:hidden">
          <TabsTrigger value="visualizer">{t('visualizer.tabVisualizer')}</TabsTrigger>
          <TabsTrigger value="packets">{t('visualizer.tabPackets')}</TabsTrigger>
        </TabsList>

        <div className="flex flex-1 overflow-hidden">
          <div
            className={cn(
              'min-w-0 flex-1 overflow-hidden transition-all duration-200',
              !fullScreen && 'lg:border-r lg:border-border',
              mobileTab !== 'visualizer' && 'hidden lg:block'
            )}
          >
            <PacketVisualizer3D
              packets={packets}
              contacts={contacts}
              config={config}
              fullScreen={fullScreen}
              onFullScreenChange={setFullScreen}
              radioOffline={radioOffline}
              directoryEnabled={directoryEnabled}
            />
          </div>

          <div
            className={cn(
              'flex flex-col overflow-hidden transition-all duration-200',
              'w-full lg:w-2/5 lg:min-w-[22rem] lg:max-w-[38rem]',
              mobileTab !== 'packets' && 'hidden',
              fullScreen ? 'lg:hidden' : 'lg:flex'
            )}
          >
            <div className="hidden border-b border-border px-3 py-2 text-sm font-medium text-muted-foreground lg:block">
              {t('visualizer.packetFeed')}
            </div>
            <div className="flex-1 overflow-hidden">
              <RawPacketList
                packets={packets}
                channels={channels}
                onPacketClick={setSelectedPacket}
                radioOffline={radioOffline}
              />
            </div>
          </div>
        </div>
      </Tabs>

      {/* While natively fullscreened, portal into the fullscreen element so the
          dialog stays visible; otherwise keep the default document.body target. */}
      <RawPacketInspectorDialog
        open={selectedPacket !== null}
        onOpenChange={(isOpen) => !isOpen && setSelectedPacket(null)}
        channels={channels}
        container={paneFullScreen ? containerRef.current : undefined}
        source={
          selectedPacket
            ? { kind: 'packet', packet: selectedPacket }
            : { kind: 'loading', message: t('rawPacket.loading') }
        }
        title={t('rawPacket.details')}
        description={t('rawPacket.detailsDescription')}
      />
    </div>
  );
}
