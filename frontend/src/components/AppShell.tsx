import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
} from 'react';
import { useSwipeable } from 'react-swipeable';

import { CommunitySetupBanner } from './CommunitySetupBanner';
import { EdgeSessionExpiredDialog } from './EdgeSessionExpiredDialog';
import { PathHashOneByteBanner } from './PathHashOneByteBanner';
import { RadioSetupBanner } from './RadioSetupBanner';
import { ServerLabelBand } from './ServerLabelBand';
import { ConversationPane } from './ConversationPane';
import { BottomNav } from './BottomNav';
import { DesktopRail } from './DesktopRail';
import { RadioStatusDialog } from './RadioStatusDialog';
import { UpdateAvailableDialog } from './UpdateAvailableDialog';
import { useOssUpdates } from '../hooks/useOssUpdates';
import { RAIL_ITEMS, type BottomNavTarget } from './navDestinations';
import { ConversationListView } from './ConversationListView';
import { DesktopListSplit } from './DesktopListSplit';
import { ToolsView } from './ToolsView';
import { SettingsIndexView } from './SettingsIndexView';
import type { NavigationData } from './navigationData';
import { countUnreadConversations } from '../utils/unreadConversations';
import { isPendingChannel, pendingChannels } from '../utils/channelMembership';
import { NewMessageModal } from './NewMessageModal';
import { BulkAddChannelResultModal } from './BulkAddChannelResultModal';
import { ContactInfoPane } from './ContactInfoPane';
import { ChannelInfoPane } from './ChannelInfoPane';
import { CommandPalette } from './CommandPalette';
import { SecurityWarningModal } from './SecurityWarningModal';
import { RadioIdentityModal } from './RadioIdentityModal';
import { Toaster } from './ui/sonner';
import {
  SETTINGS_SECTION_LABELS,
  SETTINGS_SECTION_ORDER,
  type SettingsSection,
} from './settings/settingsConstants';
import type { ServerLabel } from '../types';
import { api } from '../api';
import type { CommunityStatus, HealthStatus, RadioConfig } from '../types';
import type { CrackerPanelProps } from './CrackerPanel';
import type { SearchViewProps } from './SearchView';
import type { SettingsModalProps } from './SettingsModal';
import { ChevronLeft, PanelLeftOpen } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslation } from 'react-i18next';

const SettingsModal = lazy(() =>
  import('./SettingsModal').then((m) => ({ default: m.SettingsModal }))
);
const CrackerPanel = lazy(() =>
  import('./CrackerPanel').then((m) => ({ default: m.CrackerPanel }))
);
const SearchView = lazy(() => import('./SearchView').then((m) => ({ default: m.SearchView })));

/** The middle column is the destination's own list. A map or tool has none.
 *  Folding it is a reader preference, not a different destination. */
export function shouldShowDestinationList(target: BottomNavTarget, userCollapsed = false): boolean {
  if (userCollapsed) return false;
  return target === 'settings' || target === 'conversations';
}

type SidebarProps = NavigationData;
type ConversationPaneProps = ComponentProps<typeof ConversationPane>;
type NewMessageModalProps = Omit<ComponentProps<typeof NewMessageModal>, 'open' | 'onClose'>;
type BulkAddChannelResultModalProps = Omit<
  ComponentProps<typeof BulkAddChannelResultModal>,
  'open' | 'onClose'
>;
type ContactInfoPaneProps = ComponentProps<typeof ContactInfoPane>;
type ChannelInfoPaneProps = ComponentProps<typeof ChannelInfoPane>;

interface AppShellProps {
  serverLabel: ServerLabel;
  showNewMessage: boolean;
  showBulkAddResults: boolean;
  showSettings: boolean;
  settingsSection: SettingsSection;
  sidebarOpen: boolean;
  showCracker: boolean;
  disabledSettingsSections?: SettingsSection[];
  onSettingsSectionChange: (section: SettingsSection) => void;
  onSidebarOpenChange: (open: boolean) => void;
  onCrackerRunningChange: (running: boolean) => void;
  onToggleSettingsView: () => void;
  /** Leaves the open conversation for the list, on narrow screens. */
  onClearActiveConversation: () => void;
  onCloseSettingsView: (restoreLocation?: boolean) => void;
  onCloseNewMessage: () => void;
  onCloseBulkAddResults: () => void;
  statusProps: { health: HealthStatus | null; config: RadioConfig | null };
  /** The desktop rail's contents, in order, from the stored preferences. */
  navRailOrder?: string[];
  sidebarProps: SidebarProps;
  conversationPaneProps: ConversationPaneProps;
  searchProps: SearchViewProps;
  settingsProps: Omit<
    SettingsModalProps,
    'open' | 'pageMode' | 'externalSidebarNav' | 'desktopSection' | 'onClose'
  >;
  crackerProps: Omit<CrackerPanelProps, 'visible' | 'onRunningChange' | 'onQueueChange'>;
  newMessageModalProps: NewMessageModalProps;
  bulkAddChannelResultModalProps: BulkAddChannelResultModalProps;
  contactInfoPaneProps: ContactInfoPaneProps;
  channelInfoPaneProps: ChannelInfoPaneProps;
  onRepeaterAutoLogin: (publicKey: string, displayName: string) => void;
  onIdentityAdopted: () => void | Promise<void>;
  /** Desktop: the conversation (or settings) list column is folded away. */
  conversationListCollapsed?: boolean;
  onToggleConversationList?: () => void;
  onExpandConversationList?: () => void;
  onCommunityStatusChange?: (status: CommunityStatus) => void;
}

export function AppShell({
  serverLabel,
  showNewMessage,
  showBulkAddResults,
  showSettings,
  settingsSection,
  sidebarOpen,
  showCracker,
  disabledSettingsSections = [],
  onSettingsSectionChange,
  onSidebarOpenChange,
  onCrackerRunningChange,
  onToggleSettingsView,
  onClearActiveConversation,
  onCloseSettingsView,
  onCloseNewMessage,
  onCloseBulkAddResults,
  statusProps,
  navRailOrder,
  sidebarProps,
  conversationPaneProps,
  searchProps,
  settingsProps,
  crackerProps,
  newMessageModalProps,
  bulkAddChannelResultModalProps,
  contactInfoPaneProps,
  channelInfoPaneProps,
  onRepeaterAutoLogin,
  onIdentityAdopted,
  conversationListCollapsed = false,
  onToggleConversationList,
  onExpandConversationList,
  onCommunityStatusChange,
}: AppShellProps) {
  const { t } = useTranslation();
  const [crackerQueueCount, setCrackerQueueCount] = useState(0);

  const swipeHandlers = useSwipeable({
    onSwipedRight: ({ initial }) => {
      if (initial[0] < 30 && !sidebarOpen && window.innerWidth < 768) {
        onSidebarOpenChange(true);
      }
    },
    trackTouch: true,
    trackMouse: false,
    // These handlers sit on the app root, so they see every touch in the app — including
    // a drag down a conversation or the sidebar list. Preventing default here registers a
    // non-passive touchmove listener over the whole tree and cancels those scrolls along
    // with the edge swipe it was meant for. The gesture is recognised from its direction
    // and starting point; it does not need to suppress scrolling to do that.
    preventScrollOnSwipe: false,
  });

  const handleOpenSettings = useCallback(
    (section: SettingsSection) => {
      onSettingsSectionChange(section);
      setSettingsIndexOpen(false);
      if (!showSettings) onToggleSettingsView();
    },
    [onSettingsSectionChange, onToggleSettingsView, showSettings]
  );

  const searchMounted = useRef(false);
  if (conversationPaneProps.activeConversation?.type === 'search') {
    searchMounted.current = true;
  }

  const [identityModalForced, setIdentityModalForced] = useState(false);
  const [communityStatus, setCommunityStatus] = useState<CommunityStatus | null>(null);

  const updateCommunityStatus = useCallback(
    (status: CommunityStatus) => {
      setCommunityStatus(status);
      onCommunityStatusChange?.(status);
    },
    [onCommunityStatusChange]
  );

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve(api.getCommunity?.()).then(
      (status) => {
        if (!cancelled && status) updateCommunityStatus(status);
      },
      () => undefined
    );
    return () => {
      cancelled = true;
    };
  }, [updateCommunityStatus]);

  const crackerMounted = useRef(false);
  if (showCracker) {
    crackerMounted.current = true;
  }

  // Position toasts below the conversation header when in chat, otherwise below the status bar
  const TOAST_TOP_PADDING = 10;
  const [toastTopOffset, setToastTopOffset] = useState<number | undefined>(undefined);
  const hasLocalLabel = !!serverLabel.text;
  // Which screen the phone layout is on when no conversation or tool is open.
  const [mobileScreen, setMobileScreen] = useState<'conversations' | 'tools'>('conversations');
  // Settings open on the index on a phone: the bar cannot say which section you
  // wanted, and the rail that used to answer that went with the drawer.
  const [settingsIndexOpen, setSettingsIndexOpen] = useState(true);
  const [radioStatusOpen, setRadioStatusOpen] = useState(false);
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false);
  const ossUpdates = useOssUpdates({
    onSeeUpdate: () => handleOpenSettings('updates'),
  });
  const updateAvailable = ossUpdates.status?.update_available === true;
  const activeType = conversationPaneProps.activeConversation?.type;
  const activeId = conversationPaneProps.activeConversation?.id;

  // The bar stands down inside a conversation: there the composer owns the bottom of
  // the screen, and floating over it would either cover the send control or steal a
  // strip of history for the whole session.
  // Room servers are contacts, so they are covered by 'contact'.
  const activeChannel =
    activeType === 'channel'
      ? sidebarProps.channels.find((channel) => channel.key === activeId)
      : undefined;
  const pendingOpen = isPendingChannel(activeChannel);
  const pendingCount = pendingChannels(sidebarProps.channels).length;
  const inConversation = activeType === 'contact' || activeType === 'channel';
  // Settings render over whatever conversation was last open, so the conversation
  // underneath must not be what decides.
  const showBottomNav = showSettings || !inConversation;
  // Which of the bar's destinations is on screen. A tool opened from the Tools screen
  // keeps Tools lit, because that is where the reader came from and where Back goes.
  const TOOL_TYPES = ['raw', 'live', 'visualizer', 'trace', 'locate', 'search', 'discovered'];
  const bottomNavTarget: BottomNavTarget | null = showSettings
    ? 'settings'
    : activeType === 'map'
      ? 'map'
      : pendingOpen || (activeType && TOOL_TYPES.includes(activeType))
        ? 'tools'
        : mobileScreen === 'tools'
          ? 'tools'
          : 'conversations';
  // Counted from the conversations, not from the counter map: a counter can
  // outlive the conversation it belonged to, and counting entries then advertises
  // something the reader cannot open — the badge said one while the unread filter
  // said there was nothing.
  const unreadTotal = countUnreadConversations(
    sidebarProps.channels,
    sidebarProps.contacts,
    sidebarProps.unreadCounts ?? {}
  );
  const listAllowed = shouldShowDestinationList(bottomNavTarget);
  const showDestinationList = shouldShowDestinationList(bottomNavTarget, conversationListCollapsed);

  const handleBackToTools = useCallback(() => {
    setMobileScreen('tools');
    onClearActiveConversation();
  }, [onClearActiveConversation]);

  const handleBottomNav = useCallback(
    (target: BottomNavTarget) => {
      if (target === 'settings') {
        setSettingsIndexOpen(true);
        // Entering settings starts at the first section rather than wherever the
        // last visit ended. A section is opened to do one thing; coming back later
        // for something else and landing in it is being answered a question nobody
        // asked, and on a phone it skips the index entirely.
        onSettingsSectionChange(SETTINGS_SECTION_ORDER[0]);
        if (!showSettings) onToggleSettingsView();
        return;
      }
      // Leaving settings *for* somewhere else, so the previous location must not be
      // restored: that restoration is a history.back(), and its popstate lands
      // after the new destination and replaces it.
      if (showSettings) onCloseSettingsView(false);
      if (target === 'map') {
        setMobileScreen('conversations');
        sidebarProps.onSelectConversation({ type: 'map', id: 'map', name: 'map' } as never);
        return;
      }
      // Conversations and Tools are screens of their own, so they clear whatever
      // conversation or tool was open rather than opening another one — except
      // when the list is only folded: then Conversations means "show the list
      // again" next to the open chat, not "leave the chat".
      setMobileScreen(target === 'tools' ? 'tools' : 'conversations');
      if (target === 'conversations' && conversationListCollapsed) {
        onExpandConversationList?.();
        if (activeType === 'contact' || activeType === 'channel') return;
      }
      onClearActiveConversation();
    },
    [
      showSettings,
      onToggleSettingsView,
      onCloseSettingsView,
      onSettingsSectionChange,
      sidebarProps,
      onClearActiveConversation,
      conversationListCollapsed,
      onExpandConversationList,
      activeType,
    ]
  );
  useEffect(() => {
    const measure = () => {
      const anchor =
        document.querySelector('[data-toast-anchor="conversation"]') ??
        document.querySelector('[data-toast-anchor="statusbar"]');
      setToastTopOffset(
        anchor ? anchor.getBoundingClientRect().top + TOAST_TOP_PADDING : undefined
      );
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [hasLocalLabel, activeType, activeId, showSettings, communityStatus]);

  const destinationList = showSettings ? (
    <SettingsIndexView
      health={statusProps.health}
      onOpenRadioStatus={() => setRadioStatusOpen(true)}
      disabledSections={disabledSettingsSections}
      activeSection={settingsSection}
      updateAvailable={updateAvailable}
      onCollapseList={onToggleConversationList}
      onSelectSection={(section) => {
        onSettingsSectionChange(section);
        setSettingsIndexOpen(false);
      }}
    />
  ) : mobileScreen === 'tools' ? (
    <ToolsView
      onSelectConversation={sidebarProps.onSelectConversation}
      onMarkAllRead={sidebarProps.onMarkAllRead}
      pendingCount={pendingCount}
      health={statusProps.health}
      onOpenRadioStatus={() => setRadioStatusOpen(true)}
      updateAvailable={updateAvailable}
      activeConversation={conversationPaneProps.activeConversation}
    />
  ) : (
    <ConversationListView
      contacts={sidebarProps.contacts}
      channels={sidebarProps.channels}
      unreadCounts={sidebarProps.unreadCounts}
      mentions={sidebarProps.mentions}
      lastMessageTimes={sidebarProps.lastMessageTimes}
      lastMessagePreviews={sidebarProps.lastMessagePreviews ?? {}}
      lastMessageOutgoing={sidebarProps.lastMessageOutgoing ?? {}}
      radioName={sidebarProps.radioName}
      activeMessages={sidebarProps.activeMessages}
      onSelectConversation={sidebarProps.onSelectConversation}
      onNewMessage={sidebarProps.onNewMessage}
      health={statusProps.health}
      onOpenRadioStatus={() => setRadioStatusOpen(true)}
      updateAvailable={updateAvailable}
      activeConversation={conversationPaneProps.activeConversation}
      onCollapseList={onToggleConversationList}
      onTogglePin={sidebarProps.onTogglePin}
      onToggleFavorite={sidebarProps.onToggleFavorite}
      onMuteChannel={sidebarProps.onMuteChannel}
      onDeleteChannel={sidebarProps.onDeleteChannel}
      onDeleteContact={sidebarProps.onDeleteContact}
      onSetChannelFloodScopeOverride={sidebarProps.onSetChannelFloodScopeOverride}
      getNotifyMediaEnabled={sidebarProps.getNotifyMediaEnabled}
      onSetConversationMedia={sidebarProps.onSetConversationMediaFor}
      onOpenNotifySettings={sidebarProps.onOpenNotifySettings}
      emailReady={sidebarProps.emailReady}
      webhookReady={sidebarProps.webhookReady}
    />
  );

  return (
    <div className="relative flex flex-col h-full" {...swipeHandlers}>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:p-2 focus:bg-primary focus:text-primary-foreground"
      >
        {t('shell.skipToContent')}
      </a>
      <ServerLabelBand label={serverLabel} />

      {!(showSettings && settingsSection === 'radio') && (
        <RadioSetupBanner
          health={statusProps.health ?? null}
          onOpenRadioSettings={() => handleOpenSettings('radio')}
        />
      )}
      {!(showSettings && settingsSection === 'radio') && (
        <PathHashOneByteBanner
          config={statusProps.config}
          onOpenRadioSettings={() => handleOpenSettings('radio')}
        />
      )}
      {communityStatus && !(showSettings && settingsSection === 'community') && (
        <CommunitySetupBanner
          enabled={communityStatus.enabled}
          iata={communityStatus.iata}
          onOpenSettings={() => handleOpenSettings('community')}
        />
      )}
      <div data-toast-anchor="statusbar" aria-hidden="true" />

      <div className="flex flex-1 overflow-hidden">
        <DesktopRail
          active={bottomNavTarget}
          unreadTotal={unreadTotal}
          pendingCount={pendingCount}
          onSelect={handleBottomNav}
          health={statusProps.health ?? null}
          updateAvailable={updateAvailable}
          onOpenUpdate={() => setUpdateDialogOpen(true)}
          order={navRailOrder}
          // Settings render over whatever was open, so the pane underneath must not
          // keep the rail lit: two places cannot both be where you are.
          activeToolId={showSettings ? null : (activeType ?? null)}
          onOpenRadioStatus={() => setRadioStatusOpen(true)}
          onSelectTool={(id) => {
            const item = RAIL_ITEMS.find((candidate) => candidate.id === id);
            if (item?.overlay) {
              sidebarProps.onToggleCracker();
              return;
            }
            if (showSettings) onToggleSettingsView();
            sidebarProps.onSelectConversation(item?.conversation as never);
          }}
        />

        {/* The column belongs to the destination rather than being permanent:
            Slack, WhatsApp Desktop, Discord and VS Code all replace it when the
            section changes. A map or a tool has no list of its own — each is one
            full-width view with its own internal layout, which 384px of
            conversations was squeezing. The rail's conversations entry is the way
            back, as it is in all four. */}
        {showDestinationList && <DesktopListSplit>{destinationList}</DesktopListSplit>}
        {listAllowed && conversationListCollapsed && onToggleConversationList && (
          <button
            type="button"
            onClick={onToggleConversationList}
            aria-label={t('conversationList.expandList')}
            className="hidden h-full w-8 shrink-0 items-center justify-center border-r border-border text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:flex"
          >
            <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
          </button>
        )}

        {/* min-h-0 as well as min-w-0: a flex child defaults to min-height:auto, which
            refuses to shrink below its content. Its parent clips rather than scrolls, so
            the overflow a tall conversation produces is not a scrollbar — it is the
            composer pushed past the clip and out of reach. */}
        <main
          id="main-content"
          className={cn(
            'flex-1 flex flex-col bg-background min-w-0 min-h-0',
            showBottomNav && 'with-bottom-nav'
          )}
        >
          {/* Phones: with nothing open, the screen is the list or the tools, not an
              empty conversation pane waiting for a drawer to be opened. */}
          {!showSettings && !conversationPaneProps.activeConversation && (
            <div className="flex min-h-0 flex-1 flex-col md:hidden">{destinationList}</div>
          )}

          <div
            className={cn(
              'conversation-pane flex min-h-0 min-w-0 flex-1 flex-col',
              (showSettings || conversationPaneProps.activeConversation?.type === 'search') &&
                'hidden',
              !showSettings && !conversationPaneProps.activeConversation && 'hidden md:flex'
            )}
          >
            <ConversationPane
              {...conversationPaneProps}
              communityEnabled={communityStatus?.enabled ?? true}
              communityIata={communityStatus?.iata}
              onOpenCommunitySettings={() => handleOpenSettings('community')}
              onBack={
                pendingOpen
                  ? () =>
                      sidebarProps.onSelectConversation({
                        type: 'discovered',
                        id: 'discovered',
                        name: t('discovered.title'),
                      })
                  : onClearActiveConversation
              }
              onBackToTools={handleBackToTools}
              unreadCounts={sidebarProps.unreadCounts}
              lastMessageTimes={sidebarProps.lastMessageTimes}
              lastMessagePreviews={sidebarProps.lastMessagePreviews ?? {}}
              onToggleCracker={sidebarProps.onToggleCracker}
              crackerVisible={sidebarProps.showCracker}
              crackerQueueCount={crackerQueueCount}
            />
          </div>

          {searchMounted.current && (
            <div
              className={cn(
                'flex-1 flex flex-col min-h-0',
                (conversationPaneProps.activeConversation?.type !== 'search' || showSettings) &&
                  'hidden'
              )}
            >
              <Suspense
                fallback={
                  <div className="flex-1 flex items-center justify-center text-muted-foreground">
                    {t('shell.loadingSearch')}
                  </div>
                }
              >
                <SearchView {...searchProps} onBackToTools={handleBackToTools} />
              </Suspense>
            </div>
          )}

          {/* Phones choose a section first; desktop has the index beside the section. */}
          {showSettings && settingsIndexOpen && (
            <div className="flex min-h-0 flex-1 flex-col md:hidden">{destinationList}</div>
          )}

          {showSettings && (
            <div
              className={cn('flex-1 flex flex-col min-h-0', settingsIndexOpen && 'hidden md:flex')}
            >
              {/* A section reached from the index needs the way back the index came
                  from; desktop has the rail beside it and needs nothing. The title
                  is centred over the row rather than following the button, so it
                  stays put as sections with longer names come and go. */}
              <div className="relative flex shrink-0 items-center px-3 pb-2 pt-8 md:hidden">
                <button
                  type="button"
                  onClick={() => setSettingsIndexOpen(true)}
                  aria-label={t('settingsIndex.back')}
                  className="liquid-surface glass-back-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <ChevronLeft className="h-[1.375rem] w-[1.375rem]" aria-hidden="true" />
                </button>
                <h1 className="pointer-events-none absolute inset-x-14 truncate text-center text-base font-semibold">
                  {t(SETTINGS_SECTION_LABELS[settingsSection])}
                </h1>
              </div>
              <div className="flex-1 min-h-0 overflow-hidden">
                <Suspense
                  fallback={
                    <div className="flex-1 flex items-center justify-center p-8 text-muted-foreground">
                      {t('shell.loadingSettings')}
                    </div>
                  }
                >
                  <SettingsModal
                    {...settingsProps}
                    open={showSettings}
                    pageMode
                    externalSidebarNav
                    desktopSection={settingsSection}
                    updates={ossUpdates.status}
                    onOpenUpdate={() => setUpdateDialogOpen(true)}
                    onOpenUpdates={() => handleOpenSettings('updates')}
                    onApplyUpdate={() => void ossUpdates.apply()}
                    onAutoUpdateChange={(enabled) => void ossUpdates.setAutoUpdate(enabled)}
                    onCheckUpdates={() => void ossUpdates.checkNow()}
                    onPatchUpdateSettings={(settings) =>
                      void ossUpdates.setUpdateSettings(settings)
                    }
                    updatesChecking={ossUpdates.checking}
                    updatesApplying={ossUpdates.applying}
                    onOpenManualHelp={() => setUpdateDialogOpen(true)}
                    onClose={onCloseSettingsView}
                    onCommunityStatusChange={updateCommunityStatus}
                  />
                </Suspense>
              </div>
            </div>
          )}
        </main>
      </div>

      {showBottomNav && (
        <BottomNav
          active={bottomNavTarget}
          unreadTotal={unreadTotal}
          pendingCount={pendingCount}
          onSelect={handleBottomNav}
          updateAvailable={updateAvailable}
          onOpenUpdate={() => setUpdateDialogOpen(true)}
        />
      )}

      <div
        className={cn(
          'border-t border-border bg-background transition-all duration-200 overflow-hidden',
          showCracker ? 'h-[min(42vh,420px)]' : 'h-0'
        )}
      >
        {crackerMounted.current && (
          <Suspense
            fallback={
              <div className="flex items-center justify-center h-full text-muted-foreground">
                {t('shell.loadingChannelFinder')}
              </div>
            }
          >
            <CrackerPanel
              {...crackerProps}
              visible={showCracker}
              onRunningChange={onCrackerRunningChange}
              onQueueChange={setCrackerQueueCount}
            />
          </Suspense>
        )}
      </div>

      <RadioStatusDialog
        open={radioStatusOpen}
        health={statusProps.health ?? null}
        config={statusProps.config}
        onClose={() => setRadioStatusOpen(false)}
        onOpenRadioSettings={() => handleOpenSettings('radio')}
        onAdvertise={settingsProps.onAdvertise}
        updateAvailable={updateAvailable}
        currentVersion={ossUpdates.status?.current}
        latestVersion={ossUpdates.status?.latest}
        onOpenUpdates={() => handleOpenSettings('updates')}
      />
      <UpdateAvailableDialog
        open={updateDialogOpen || ossUpdates.showProgress}
        updates={ossUpdates.status}
        applying={ossUpdates.applying}
        progressPercent={ossUpdates.progressPercent}
        progressPhase={ossUpdates.progressPhase}
        applyError={ossUpdates.applyError}
        onApply={() => void ossUpdates.apply()}
        onAutoUpdate={(enabled) => void ossUpdates.setAutoUpdate(enabled)}
        onClose={() => {
          setUpdateDialogOpen(false);
          ossUpdates.dismissProgress();
        }}
      />

      <NewMessageModal
        {...newMessageModalProps}
        open={showNewMessage}
        onClose={onCloseNewMessage}
      />
      <BulkAddChannelResultModal
        {...bulkAddChannelResultModalProps}
        open={showBulkAddResults}
        onClose={onCloseBulkAddResults}
      />

      <CommandPalette
        contacts={sidebarProps.contacts}
        channels={sidebarProps.channels}
        onSelectConversation={sidebarProps.onSelectConversation}
        onOpenSettings={handleOpenSettings}
        onRepeaterAutoLogin={onRepeaterAutoLogin}
      />
      <EdgeSessionExpiredDialog />
      <SecurityWarningModal health={statusProps.health} />
      <RadioIdentityModal
        health={statusProps.health}
        forceOpen={identityModalForced}
        onAdopted={onIdentityAdopted}
        onResolved={async () => {
          setIdentityModalForced(false);
          await settingsProps.onHealthRefresh();
        }}
      />
      <ContactInfoPane {...contactInfoPaneProps} />
      <ChannelInfoPane {...channelInfoPaneProps} />
      <Toaster
        position="top-right"
        offset={toastTopOffset !== undefined ? { top: toastTopOffset } : undefined}
        mobileOffset={toastTopOffset !== undefined ? { top: toastTopOffset } : undefined}
      />
    </div>
  );
}
