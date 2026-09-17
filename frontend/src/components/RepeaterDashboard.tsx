import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../api';
import { Button } from './ui/button';
import { ChevronLeft, Info, MoreVertical, Route, Star, Trash2 } from 'lucide-react';
import { DirectTraceIcon } from './DirectTraceIcon';
import { RepeaterLogin } from './RepeaterLogin';
import { ServerLoginStatusBanner } from './ServerLoginStatusBanner';
import { useRememberedServerPassword } from '../hooks/useRememberedServerPassword';
import { useRepeaterDashboard } from '../hooks/useRepeaterDashboard';
import { isValidLocation } from '../utils/pathUtils';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Contact, Conversation, PathDiscoveryResponse, TelemetryHistoryEntry } from '../types';
import { cn } from '../lib/utils';
import { TelemetryPane } from './repeater/RepeaterTelemetryPane';
import { NeighborsPane } from './repeater/RepeaterNeighborsPane';
import { AclPane } from './repeater/RepeaterAclPane';
import { NodeInfoPane } from './repeater/RepeaterNodeInfoPane';
import { RadioSettingsPane } from './repeater/RepeaterRadioSettingsPane';
import { LppTelemetryPane } from './repeater/RepeaterLppTelemetryPane';
import { OwnerInfoPane } from './repeater/RepeaterOwnerInfoPane';
import { RegionsPane } from './repeater/RepeaterRegionsPane';
import { ActionsPane } from './repeater/RepeaterActionsPane';
import { ConsolePane } from './repeater/RepeaterConsolePane';
import { TelemetryHistoryPane } from './repeater/RepeaterTelemetryHistoryPane';
import { ContactPathDiscoveryModal } from './ContactPathDiscoveryModal';

// Re-export for backwards compatibility (used by repeaterFormatters.test.ts)
export { formatDuration, formatClockDrift } from './repeater/repeaterPaneShared';

// --- Main Dashboard ---

interface RepeaterDashboardProps {
  /** Leaves this pane, which stands in for the conversation and its header. */
  onBack?: () => void;
  conversation: Conversation;
  contacts: Contact[];
  radioLat: number | null;
  radioLon: number | null;
  radioName: string | null;
  onTrace: () => void;
  onPathDiscovery: (publicKey: string) => Promise<PathDiscoveryResponse>;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onDeleteContact: (publicKey: string) => void;
  onOpenContactInfo?: (publicKey: string) => void;
  trackedTelemetryRepeaters: string[];
  onToggleTrackedTelemetry: (publicKey: string) => Promise<void>;
  autoLoginAndLoadAll?: boolean;
  onAutoLoginConsumed?: () => void;
}

export function RepeaterDashboard({
  onBack,
  conversation,
  contacts,
  radioLat,
  radioLon,
  radioName,
  onTrace,
  onPathDiscovery,
  onToggleFavorite,
  onDeleteContact,
  onOpenContactInfo,
  trackedTelemetryRepeaters,
  onToggleTrackedTelemetry,
  autoLoginAndLoadAll,
  onAutoLoginConsumed,
}: RepeaterDashboardProps) {
  const { t } = useTranslation();
  const [actionsOpen, setActionsOpen] = useState(false);
  const [pathDiscoveryOpen, setPathDiscoveryOpen] = useState(false);
  const contact = contacts.find((c) => c.public_key === conversation.id) ?? null;
  const hasAdvertLocation = isValidLocation(contact?.lat ?? null, contact?.lon ?? null);
  const {
    loggedIn,
    loginLoading,
    loginError,
    lastLoginAttempt,
    paneData,
    paneStates,
    consoleHistory,
    consoleLoading,
    login,
    loginAsGuest,
    resetLogin,
    refreshPane,
    loadAll,
    cancelLoadAll,
    loadAllProgress,
    sendConsoleCommand,
    sendZeroHopAdvert,
    sendFloodAdvert,
    rebootRepeater,
    syncClock,
  } = useRepeaterDashboard(conversation, { hasAdvertLocation });
  const {
    password,
    setPassword,
    rememberPassword,
    setRememberPassword,
    persistAfterLogin,
    forgetPassword,
  } = useRememberedServerPassword('repeater', conversation.id);

  // Telemetry history: preload from stored data, refresh from live status
  const [telemetryHistory, setTelemetryHistory] = useState<TelemetryHistoryEntry[]>([]);
  const telemetryHistorySourceRef = useRef<'none' | 'preload' | 'live'>('none');
  const telemetryHistoryRequestRef = useRef(0);

  useEffect(() => {
    telemetryHistoryRequestRef.current += 1;
    telemetryHistorySourceRef.current = 'none';
    setTelemetryHistory([]);

    if (!loggedIn) return;

    const requestId = telemetryHistoryRequestRef.current;
    api
      .repeaterTelemetryHistory(conversation.id)
      .then((history) => {
        if (telemetryHistoryRequestRef.current !== requestId) return;
        if (telemetryHistorySourceRef.current === 'live') return;
        telemetryHistorySourceRef.current = 'preload';
        setTelemetryHistory(history);
      })
      .catch(() => {});
  }, [loggedIn, conversation.id]);

  // When a live status fetch returns embedded telemetry_history, replace local state
  useEffect(() => {
    const liveHistory = paneData.status?.telemetry_history;
    if (!liveHistory) return;
    telemetryHistorySourceRef.current = 'live';
    setTelemetryHistory(liveHistory);
  }, [paneData.status?.telemetry_history]);

  // Command palette "ACL login + load all" auto-action
  const autoLoginConsumedRef = useRef(false);
  useEffect(() => {
    if (!autoLoginAndLoadAll || autoLoginConsumedRef.current) return;
    autoLoginConsumedRef.current = true;
    onAutoLoginConsumed?.();
    void loginAsGuest().then(() => loadAll());
  }, [autoLoginAndLoadAll, onAutoLoginConsumed, loginAsGuest, loadAll]);

  useEffect(() => {
    setActionsOpen(false);
    setPathDiscoveryOpen(false);
  }, [conversation.id]);

  const isFav = contact?.favorite ?? false;

  const headerActionClass =
    'inline-flex h-9 w-9 items-center justify-center rounded p-2 text-lg leading-none transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

  const handleRepeaterLogin = async (nextPassword: string) => {
    await login(nextPassword);
    persistAfterLogin(nextPassword);
  };
  const handleRepeaterGuestLogin = async () => {
    await loginAsGuest();
    persistAfterLogin('');
  };
  const handleReenterPassword = () => {
    forgetPassword();
    resetLogin();
  };

  // Loading all panes indicator
  const anyLoading = Object.values(paneStates).some((s) => s.loading);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <header className="conversation-header grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-0.5 border-b border-border/30 px-4 py-2.5">
        <span className="flex min-w-0 items-center gap-2">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              aria-label={t('shell.backToConversations')}
              className="liquid-surface glass-back-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronLeft className="h-[1.375rem] w-[1.375rem]" aria-hidden="true" />
            </button>
          )}
          <h2 className="min-w-0 flex-1 font-semibold text-base">
            {onOpenContactInfo ? (
              <button
                type="button"
                className="flex max-w-full min-w-0 items-center gap-1.5 overflow-hidden rounded-sm text-left transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={t('repeater.viewInfo', { name: conversation.name })}
                onClick={() => onOpenContactInfo(conversation.id)}
              >
                <span className="truncate">{conversation.name}</span>
                <Info
                  className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/80"
                  aria-hidden="true"
                />
              </button>
            ) : (
              <span className="truncate">{conversation.name}</span>
            )}
          </h2>
        </span>
        <div className="relative flex items-center justify-end gap-1.5">
          {loggedIn && loadAllProgress && (
            <div className="flex items-center gap-1.5">
              <span className="whitespace-nowrap text-[0.6875rem] tabular-nums text-muted-foreground sm:text-xs">
                {t('repeater.loadAllProgress', loadAllProgress)}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={cancelLoadAll}
                className="h-7 px-2 text-[0.6875rem] leading-none sm:h-8 sm:px-3 sm:text-xs"
              >
                {t('repeater.stopLoading')}
              </Button>
            </div>
          )}
          <button
            type="button"
            onClick={() => setActionsOpen((open) => !open)}
            aria-expanded={actionsOpen}
            aria-label={t('chatHeader.moreActions')}
            className={cn(headerActionClass, 'conversation-header-more')}
          >
            <MoreVertical className="h-4 w-4" aria-hidden="true" />
          </button>
          <div className={cn('conversation-header-actions', actionsOpen && 'is-open')}>
            {loggedIn && !loadAllProgress && (
              <Button
                variant="outline"
                size="sm"
                onClick={loadAll}
                disabled={anyLoading}
                className="h-7 border-success px-2 text-[0.6875rem] leading-none text-success hover:bg-success/10 hover:text-success sm:h-8 sm:px-3 sm:text-xs"
              >
                {anyLoading ? t('repeater.loading') : t('repeater.loadAll')}
              </Button>
            )}
            {contact && (
              <button
                className={headerActionClass}
                onClick={() => setPathDiscoveryOpen(true)}
                title={t('repeater.pathDiscoveryTitle')}
                aria-label={t('repeater.pathDiscovery')}
              >
                <Route className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </button>
            )}
            <button
              className={headerActionClass}
              onClick={onTrace}
              title={t('repeater.directTrace')}
              aria-label={t('repeater.directTrace')}
            >
              <DirectTraceIcon className="h-4 w-4 text-muted-foreground" />
            </button>
            <button
              className={headerActionClass}
              onClick={() => onToggleFavorite('contact', conversation.id)}
              title={isFav ? t('repeater.favoriteRemoveTitle') : t('repeater.favoriteAddTitle')}
              aria-label={isFav ? t('repeater.favoriteRemove') : t('repeater.favoriteAdd')}
            >
              {isFav ? (
                <Star className="h-4 w-4 fill-current text-favorite" aria-hidden="true" />
              ) : (
                <Star className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              )}
            </button>
            <button
              className={cn(
                headerActionClass,
                'ml-1.5 hover:bg-destructive/10 text-muted-foreground hover:text-destructive'
              )}
              onClick={() => onDeleteContact(conversation.id)}
              title={t('repeater.delete')}
              aria-label={t('repeater.delete')}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
        {contact && (
          <div className="col-span-2 min-w-0 truncate whitespace-nowrap text-[0.6875rem] text-muted-foreground">
            <ContactStatusInfo contact={contact} ourLat={radioLat} ourLon={radioLon} />
          </div>
        )}
        {contact && (
          <ContactPathDiscoveryModal
            open={pathDiscoveryOpen}
            onClose={() => setPathDiscoveryOpen(false)}
            contact={contact}
            contacts={contacts}
            radioName={radioName}
            onDiscover={onPathDiscovery}
          />
        )}
      </header>
      <div data-toast-anchor="conversation" aria-hidden="true" />

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4">
        {!loggedIn ? (
          <RepeaterLogin
            repeaterName={conversation.name}
            loading={loginLoading}
            error={loginError}
            password={password}
            onPasswordChange={setPassword}
            rememberPassword={rememberPassword}
            onRememberPasswordChange={setRememberPassword}
            onLogin={handleRepeaterLogin}
            onLoginAsGuest={handleRepeaterGuestLogin}
          />
        ) : (
          <div className="space-y-4">
            <ServerLoginStatusBanner
              attempt={lastLoginAttempt}
              loading={loginLoading}
              canRetryPassword={password.trim().length > 0}
              onRetryPassword={() => handleRepeaterLogin(password)}
              onRetryBlank={handleRepeaterGuestLogin}
              onReenterPassword={handleReenterPassword}
              blankRetryLabel={t('repeater.retryExistingAccess')}
            />
            {/* Top row: Telemetry + Radio Settings | Node Info + Neighbors */}
            <div className="repeater-dashboard-grid">
              <div className="flex flex-col gap-4">
                <NodeInfoPane
                  data={paneData.nodeInfo}
                  state={paneStates.nodeInfo}
                  onRefresh={() => refreshPane('nodeInfo')}
                  disabled={anyLoading}
                />
                <TelemetryPane
                  data={paneData.status}
                  state={paneStates.status}
                  onRefresh={() => refreshPane('status')}
                  disabled={anyLoading}
                />
                <RadioSettingsPane
                  data={paneData.radioSettings}
                  state={paneStates.radioSettings}
                  onRefresh={() => refreshPane('radioSettings')}
                  disabled={anyLoading}
                  advertData={paneData.advertIntervals}
                  advertState={paneStates.advertIntervals}
                  onRefreshAdvert={() => refreshPane('advertIntervals')}
                />
                <LppTelemetryPane
                  data={paneData.lppTelemetry}
                  state={paneStates.lppTelemetry}
                  onRefresh={() => refreshPane('lppTelemetry')}
                  disabled={anyLoading}
                />
              </div>
              <div className="flex min-h-0 flex-col gap-4">
                <NeighborsPane
                  data={paneData.neighbors}
                  state={paneStates.neighbors}
                  onRefresh={() => refreshPane('neighbors')}
                  disabled={anyLoading}
                  repeaterContact={contact}
                  contacts={contacts}
                  nodeInfo={paneData.nodeInfo}
                  nodeInfoState={paneStates.nodeInfo}
                  repeaterName={conversation.name}
                />
              </div>
            </div>

            {/* Remaining panes: ACL + Regions | Owner Info + Actions */}
            <div className="repeater-dashboard-grid">
              <div className="flex flex-col gap-4">
                <AclPane
                  data={paneData.acl}
                  state={paneStates.acl}
                  onRefresh={() => refreshPane('acl')}
                  disabled={anyLoading}
                />
                <RegionsPane
                  data={paneData.regions}
                  state={paneStates.regions}
                  onRefresh={() => refreshPane('regions')}
                  disabled={anyLoading}
                />
              </div>
              <div className="flex flex-col gap-4">
                <OwnerInfoPane
                  data={paneData.ownerInfo}
                  state={paneStates.ownerInfo}
                  onRefresh={() => refreshPane('ownerInfo')}
                  disabled={anyLoading}
                />
                <ActionsPane
                  onSendZeroHopAdvert={sendZeroHopAdvert}
                  onSendFloodAdvert={sendFloodAdvert}
                  onSyncClock={syncClock}
                  onReboot={rebootRepeater}
                  consoleLoading={consoleLoading}
                />
              </div>
            </div>

            {/* Console — full width */}
            <ConsolePane
              history={consoleHistory}
              loading={consoleLoading}
              onSend={sendConsoleCommand}
            />

            {/* Telemetry history chart — full width, below console */}
            <TelemetryHistoryPane
              entries={telemetryHistory}
              publicKey={conversation.id}
              contacts={contacts}
              trackedTelemetryRepeaters={trackedTelemetryRepeaters}
              onToggleTrackedTelemetry={onToggleTrackedTelemetry}
            />
          </div>
        )}
      </div>
    </div>
  );
}
