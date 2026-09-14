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
import { StatusBar } from './StatusBar';
import { Sidebar } from './Sidebar';
import { ConversationPane } from './ConversationPane';
import { BottomNav, type BottomNavTarget } from './BottomNav';
import { ConversationListView } from './ConversationListView';
import { ToolsView } from './ToolsView';
import { SettingsIndexView } from './SettingsIndexView';
import { NewMessageModal } from './NewMessageModal';
import { BulkAddChannelResultModal } from './BulkAddChannelResultModal';
import { ContactInfoPane } from './ContactInfoPane';
import { ChannelInfoPane } from './ChannelInfoPane';
import { CommandPalette } from './CommandPalette';
import { SecurityWarningModal } from './SecurityWarningModal';
import { RadioIdentityModal } from './RadioIdentityModal';
import { Toaster } from './ui/sonner';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import {
  SETTINGS_SECTION_ICONS,
  SETTINGS_SECTION_LABELS,
  SETTINGS_SECTION_ORDER,
  type SettingsSection,
} from './settings/settingsConstants';
import { getContrastTextColor, type LocalLabel } from '../utils/localLabel';
import {
  getSavedDesktopSidebarCollapsed,
  setSavedDesktopSidebarCollapsed,
} from '../utils/sidebarRailPreference';
import { api } from '../api';
import type { CommunityStatus } from '../types';
import type { CrackerPanelProps } from './CrackerPanel';
import type { SearchViewProps } from './SearchView';
import type { SettingsModalProps } from './SettingsModal';
import { cn } from '@/lib/utils';
import { PanelLeftClose, PanelLeftOpen, ChevronLeft } from 'lucide-react';
import { useTranslation } from 'react-i18next';

const SettingsModal = lazy(() =>
  import('./SettingsModal').then((m) => ({ default: m.SettingsModal }))
);
const CrackerPanel = lazy(() =>
  import('./CrackerPanel').then((m) => ({ default: m.CrackerPanel }))
);
const SearchView = lazy(() => import('./SearchView').then((m) => ({ default: m.SearchView })));

type SidebarProps = ComponentProps<typeof Sidebar>;
type ConversationPaneProps = ComponentProps<typeof ConversationPane>;
type NewMessageModalProps = Omit<ComponentProps<typeof NewMessageModal>, 'open' | 'onClose'>;
type BulkAddChannelResultModalProps = Omit<
  ComponentProps<typeof BulkAddChannelResultModal>,
  'open' | 'onClose'
>;
type ContactInfoPaneProps = ComponentProps<typeof ContactInfoPane>;
type ChannelInfoPaneProps = ComponentProps<typeof ChannelInfoPane>;

interface AppShellProps {
  localLabel: LocalLabel;
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
  onCloseSettingsView: () => void;
  onCloseNewMessage: () => void;
  onCloseBulkAddResults: () => void;
  onLocalLabelChange: (label: LocalLabel) => void;
  statusProps: Pick<ComponentProps<typeof StatusBar>, 'health' | 'config'>;
  sidebarProps: SidebarProps;
  conversationPaneProps: ConversationPaneProps;
  searchProps: SearchViewProps;
  settingsProps: Omit<
    SettingsModalProps,
    'open' | 'pageMode' | 'externalSidebarNav' | 'desktopSection' | 'onClose' | 'onLocalLabelChange'
  >;
  crackerProps: Omit<CrackerPanelProps, 'visible' | 'onRunningChange' | 'onQueueChange'>;
  newMessageModalProps: NewMessageModalProps;
  bulkAddChannelResultModalProps: BulkAddChannelResultModalProps;
  contactInfoPaneProps: ContactInfoPaneProps;
  channelInfoPaneProps: ChannelInfoPaneProps;
  onRepeaterAutoLogin: (publicKey: string, displayName: string) => void;
  onIdentityAdopted: () => void | Promise<void>;
}

export function AppShell({
  localLabel,
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
  onLocalLabelChange,
  statusProps,
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
}: AppShellProps) {
  const { t } = useTranslation();
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState(
    getSavedDesktopSidebarCollapsed
  );
  const [crackerQueueCount, setCrackerQueueCount] = useState(0);

  const handleToggleDesktopSidebar = useCallback(() => {
    setDesktopSidebarCollapsed((prev) => {
      const next = !prev;
      setSavedDesktopSidebarCollapsed(next);
      return next;
    });
  }, []);

  const desktopSidebarWidthClass = desktopSidebarCollapsed ? 'w-full md:w-14' : 'w-60';

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

  const closeSwipeHandlers = useSwipeable({
    onSwipedLeft: () => onSidebarOpenChange(false),
    trackTouch: true,
    trackMouse: false,
    preventScrollOnSwipe: false,
  });

  const handleOpenSettings = useCallback(
    (section: SettingsSection) => {
      onSettingsSectionChange(section);
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

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve(api.getCommunity?.()).then(
      (status) => {
        if (!cancelled && status) setCommunityStatus(status);
      },
      () => undefined
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const crackerMounted = useRef(false);
  if (showCracker) {
    crackerMounted.current = true;
  }

  // Position toasts below the conversation header when in chat, otherwise below the status bar
  const TOAST_TOP_PADDING = 10;
  const [toastTopOffset, setToastTopOffset] = useState<number | undefined>(undefined);
  const hasLocalLabel = !!localLabel.text;
  // Which screen the phone layout is on when no conversation or tool is open.
  const [mobileScreen, setMobileScreen] = useState<'conversations' | 'tools'>('conversations');
  // Settings open on the index on a phone: the bar cannot say which section you
  // wanted, and the rail that used to answer that went with the drawer.
  const [settingsIndexOpen, setSettingsIndexOpen] = useState(true);
  const activeType = conversationPaneProps.activeConversation?.type;
  const activeId = conversationPaneProps.activeConversation?.id;

  // The bar stands down inside a conversation: there the composer owns the bottom of
  // the screen, and floating over it would either cover the send control or steal a
  // strip of history for the whole session.
  // Room servers are contacts, so they are covered by 'contact'.
  const inConversation = activeType === 'contact' || activeType === 'channel';
  // Settings render over whatever conversation was last open, so the conversation
  // underneath must not be what decides.
  const showBottomNav = showSettings || !inConversation;
  // Which of the bar's destinations is on screen. A tool opened from the Tools screen
  // keeps Tools lit, because that is where the reader came from and where Back goes.
  const TOOL_TYPES = ['raw', 'visualizer', 'trace', 'locate', 'search'];
  const bottomNavTarget: BottomNavTarget | null = showSettings
    ? 'settings'
    : activeType === 'map'
      ? 'map'
      : activeType && TOOL_TYPES.includes(activeType)
        ? 'tools'
        : mobileScreen === 'tools'
          ? 'tools'
          : 'conversations';
  const unreadTotal = Object.values(sidebarProps.unreadCounts ?? {}).reduce(
    (sum, n) => sum + (n > 0 ? 1 : 0),
    0
  );

  const handleBottomNav = useCallback(
    (target: BottomNavTarget) => {
      if (target === 'settings') {
        setSettingsIndexOpen(true);
        if (!showSettings) onToggleSettingsView();
        return;
      }
      if (showSettings) onToggleSettingsView();
      if (target === 'map') {
        setMobileScreen('conversations');
        sidebarProps.onSelectConversation({ type: 'map', id: 'map', name: 'map' } as never);
        return;
      }
      // Conversations and Tools are screens of their own, so they clear whatever
      // conversation or tool was open rather than opening another one.
      setMobileScreen(target === 'tools' ? 'tools' : 'conversations');
      onClearActiveConversation();
    },
    [showSettings, onToggleSettingsView, sidebarProps, onClearActiveConversation]
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

  const settingsSidebarContent = (
    <nav
      className={cn(
        'sidebar h-full min-h-0 overflow-hidden bg-card border-r border-border flex flex-col',
        desktopSidebarWidthClass
      )}
      aria-label={t('settingsNav.aria')}
      data-desktop-collapsed={desktopSidebarCollapsed ? 'true' : undefined}
    >
      <div
        className={cn(
          'flex justify-between items-center px-3 py-2.5 border-b border-border',
          desktopSidebarCollapsed && 'md:flex-col md:items-center md:gap-1.5 md:px-1.5'
        )}
      >
        <h2
          className={cn(
            'text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium',
            desktopSidebarCollapsed && 'md:hidden'
          )}
        >
          {t('settingsNav.title')}
        </h2>
        {/* The status bar already carries the way out of settings, and it is visible
            on every viewport. A second identical control 60px away is just noise. */}
        <div
          className={cn(
            'flex items-center gap-1',
            desktopSidebarCollapsed && 'md:flex-col md:gap-1.5'
          )}
        >
          <button
            type="button"
            onClick={handleToggleDesktopSidebar}
            className="hidden md:inline-flex h-8 w-8 items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            title={desktopSidebarCollapsed ? t('sidebar.expand') : t('sidebar.collapse')}
            aria-label={desktopSidebarCollapsed ? t('sidebar.expand') : t('sidebar.collapse')}
            aria-expanded={!desktopSidebarCollapsed}
          >
            {desktopSidebarCollapsed ? (
              <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
            ) : (
              <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto py-1 [contain:layout_paint]">
        {SETTINGS_SECTION_ORDER.map((section) => {
          const Icon = SETTINGS_SECTION_ICONS[section];
          const disabled = disabledSettingsSections.includes(section);
          return (
            <button
              key={section}
              type="button"
              disabled={disabled}
              title={t(SETTINGS_SECTION_LABELS[section])}
              className={cn(
                'w-full px-3 py-2 text-left text-[0.8125rem] border-l-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset disabled:cursor-not-allowed disabled:opacity-50',
                !disabled && 'hover:bg-accent',
                settingsSection === section && !disabled && 'bg-accent border-l-primary',
                desktopSidebarCollapsed && 'md:px-1.5 md:justify-center'
              )}
              aria-current={settingsSection === section ? 'true' : undefined}
              onClick={() => onSettingsSectionChange(section)}
            >
              <span
                className={cn(
                  'flex items-center gap-2',
                  desktopSidebarCollapsed && 'md:justify-center'
                )}
              >
                <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                <span className={cn(desktopSidebarCollapsed && 'md:hidden')}>
                  {t(SETTINGS_SECTION_LABELS[section])}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );

  const activeSidebarContent = showSettings ? (
    settingsSidebarContent
  ) : (
    <Sidebar
      {...sidebarProps}
      crackerQueueCount={crackerQueueCount}
      desktopCollapsed={desktopSidebarCollapsed}
      onToggleDesktopCollapsed={handleToggleDesktopSidebar}
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
      {localLabel.text && (
        <div
          style={{
            backgroundColor: localLabel.color,
            color: getContrastTextColor(localLabel.color),
          }}
          className="px-4 py-1 text-center text-sm font-medium"
        >
          {localLabel.text}
        </div>
      )}

      {/* Desktop only. On a phone its whole job has moved: navigation to the bar,
          the way back into the conversation's own header, the radio state onto the
          screens that need it, and settings and theme into Settings. What was left
          was a bar saying the app's name. */}
      <StatusBar
        className="hidden md:flex"
        health={statusProps.health}
        config={statusProps.config}
        settingsMode={showSettings}
        onSettingsClick={onToggleSettingsView}
        onOpenRadioSettings={() => handleOpenSettings('radio')}
        onOpenIdentityModal={() => setIdentityModalForced(true)}
      />
      {communityStatus && !(showSettings && settingsSection === 'community') && (
        <CommunitySetupBanner
          enabled={communityStatus.enabled}
          iata={communityStatus.iata}
          onOpenSettings={() => handleOpenSettings('community')}
        />
      )}
      <div data-toast-anchor="statusbar" aria-hidden="true" />

      <div className="flex flex-1 overflow-hidden">
        <div className="hidden md:block min-h-0 overflow-hidden">{activeSidebarContent}</div>

        {/* Desktop keeps the drawer for the settings rail; phones never open it. */}
        <Sheet open={sidebarOpen && showSettings} onOpenChange={onSidebarOpenChange}>
          <SheetContent
            side="left"
            className="w-[280px] p-0 flex flex-col"
            hideCloseButton
            onOpenAutoFocus={(event) => {
              event.preventDefault();
            }}
          >
            <SheetHeader className="sr-only">
              <SheetTitle>{t('shell.navigation')}</SheetTitle>
              <SheetDescription>{t('shell.sidebarNav')}</SheetDescription>
            </SheetHeader>
            <div className="flex-1 overflow-hidden" {...closeSwipeHandlers}>
              {activeSidebarContent}
            </div>
          </SheetContent>
        </Sheet>

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
            <div className="flex min-h-0 flex-1 flex-col md:hidden">
              {mobileScreen === 'tools' ? (
                <ToolsView
                  onSelectConversation={sidebarProps.onSelectConversation}
                  onToggleCracker={sidebarProps.onToggleCracker}
                  onMarkAllRead={sidebarProps.onMarkAllRead}
                  crackerVisible={sidebarProps.showCracker}
                  health={statusProps.health}
                  onOpenRadioSettings={() => handleOpenSettings('radio')}
                />
              ) : (
                <ConversationListView
                  contacts={sidebarProps.contacts}
                  channels={sidebarProps.channels}
                  unreadCounts={sidebarProps.unreadCounts}
                  mentions={sidebarProps.mentions}
                  lastMessageTimes={sidebarProps.lastMessageTimes}
                  lastMessagePreviews={sidebarProps.lastMessagePreviews ?? {}}
                  onSelectConversation={sidebarProps.onSelectConversation}
                  onNewMessage={sidebarProps.onNewMessage}
                  health={statusProps.health}
                  onOpenRadioSettings={() => handleOpenSettings('radio')}
                />
              )}
            </div>
          )}

          <div
            className={cn(
              'flex-1 flex flex-col min-h-0',
              (showSettings || conversationPaneProps.activeConversation?.type === 'search') &&
                'hidden',
              !showSettings && !conversationPaneProps.activeConversation && 'hidden md:flex'
            )}
          >
            <ConversationPane
              {...conversationPaneProps}
              communityEnabled={communityStatus?.enabled ?? true}
              onBack={onClearActiveConversation}
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
                <SearchView {...searchProps} />
              </Suspense>
            </div>
          )}

          {/* Phones choose a section first; desktop has the rail and goes straight in. */}
          {showSettings && settingsIndexOpen && (
            <div className="flex min-h-0 flex-1 flex-col md:hidden">
              <SettingsIndexView
                health={statusProps.health}
                disabledSections={disabledSettingsSections}
                onSelectSection={(section) => {
                  onSettingsSectionChange(section);
                  setSettingsIndexOpen(false);
                }}
              />
            </div>
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
                  className="liquid-surface inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                    onClose={onCloseSettingsView}
                    onLocalLabelChange={onLocalLabelChange}
                    onCommunityStatusChange={setCommunityStatus}
                  />
                </Suspense>
              </div>
            </div>
          )}
        </main>
      </div>

      {showBottomNav && (
        <BottomNav active={bottomNavTarget} unreadTotal={unreadTotal} onSelect={handleBottomNav} />
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
