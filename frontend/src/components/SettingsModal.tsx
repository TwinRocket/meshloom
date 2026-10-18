import { lazy, Suspense, useState, useEffect, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  Contact,
  HealthStatus,
  OssUpdateSettingsPatch,
  OssUpdateStatus,
  RadioAdvertMode,
  RadioConfig,
  RadioConfigUpdate,
  RadioDiscoveryResponse,
  RadioDiscoveryTarget,
  RadioReconnectResponse,
  RadioRegionDiscoveryResponse,
} from '../types';
import {
  SETTINGS_SECTION_ICONS,
  SETTINGS_SECTION_LABELS,
  type SettingsSection,
} from './settings/settingsConstants';

import { SettingsRadioSection } from './settings/SettingsRadioSection';
import { SettingsProxySection } from './settings/SettingsProxySection';
import { SettingsLocalSection } from './settings/SettingsLocalSection';
import { SettingsNotificationsSection } from './settings/SettingsNotificationsSection';
import { SettingsCommunitySection } from './settings/SettingsCommunitySection';
import { SettingsShareSection } from './settings/SettingsShareSection';
import { SettingsRadioAppSection } from './settings/SettingsRadioAppSection';
import { SettingsAlertsSection } from './settings/SettingsAlertsSection';
import { SettingsFanoutSection } from './settings/SettingsFanoutSection';
import { SettingsDatabaseSection } from './settings/SettingsDatabaseSection';
import { SettingsNavigationSection } from './settings/SettingsNavigationSection';
import { SettingsStatisticsSection } from './settings/SettingsStatisticsSection';
import { SettingsAboutSection } from './settings/SettingsAboutSection';
import { SettingsUpdatesSection } from './settings/SettingsUpdatesSection';
import { getSettingsHash } from '../utils/urlHash';
import { useCapabilities } from '../auth/SessionProvider';

const SettingsAccessSection = lazy(() =>
  import('./settings/SettingsAccessSection').then((m) => ({ default: m.SettingsAccessSection }))
);

interface SettingsModalBaseProps {
  open: boolean;
  pageMode?: boolean;
  config: RadioConfig | null;
  health: HealthStatus | null;
  appSettings: AppSettings | null;
  onClose: () => void;
  onSave: (update: RadioConfigUpdate) => Promise<void>;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onSetPrivateKey: (key: string) => Promise<void>;
  onReboot: () => Promise<void>;
  onDisconnect: () => Promise<void>;
  /** Resolves with the backend answer: `code: 'radio_rebooting'` means the radio is
   * restarting after a clock fix and comes back on its own. */
  onReconnect: () => Promise<RadioReconnectResponse | void>;
  onAdvertise: (mode: RadioAdvertMode) => Promise<void>;
  meshDiscovery: RadioDiscoveryResponse | null;
  meshDiscoveryLoadingTarget: RadioDiscoveryTarget | null;
  onDiscoverMesh: (target: RadioDiscoveryTarget) => Promise<void>;
  regionDiscovery: RadioRegionDiscoveryResponse | null;
  regionDiscoveryLoading: boolean;
  onDiscoverRegions: (publicKeys?: string[]) => Promise<void>;
  onHealthRefresh: () => Promise<void>;
  /** Reload the client data after a backup restore (see SettingsDatabaseSection). */
  onBackupRestored?: (identityChanged: boolean) => Promise<void>;
  onRefreshAppSettings: () => Promise<void>;
  blockedKeys?: string[];
  blockedNames?: string[];
  onToggleBlockedKey?: (key: string) => void;
  onToggleBlockedName?: (name: string) => void;
  contacts?: Contact[];
  channels?: Channel[];
  onBulkDeleteContacts?: (deletedKeys: string[]) => void;
  trackedTelemetryRepeaters?: string[];
  onToggleTrackedTelemetry?: (publicKey: string) => Promise<void>;
  trackedTelemetryContacts?: string[];
  onToggleTrackedTelemetryContact?: (publicKey: string) => Promise<void>;
  updates?: OssUpdateStatus | null;
  onOpenUpdate?: () => void;
  onOpenUpdates?: () => void;
  onApplyUpdate?: () => void;
  onAutoUpdateChange?: (enabled: boolean) => void;
  onCheckUpdates?: () => void;
  onPatchUpdateSettings?: (settings: OssUpdateSettingsPatch) => void;
  updatesChecking?: boolean;
  updatesApplying?: boolean;
  onOpenManualHelp?: () => void;
}

export type SettingsModalProps = SettingsModalBaseProps &
  (
    | { externalSidebarNav: true; desktopSection: SettingsSection }
    | { externalSidebarNav?: false; desktopSection?: never }
  );

export function SettingsModal(props: SettingsModalProps) {
  const {
    open,
    pageMode = false,
    config,
    health,
    appSettings,
    onClose,
    onSave,
    onSaveAppSettings,
    onSetPrivateKey,
    onReboot,
    onDisconnect,
    onReconnect,
    onAdvertise,
    meshDiscovery,
    meshDiscoveryLoadingTarget,
    onDiscoverMesh,
    regionDiscovery,
    regionDiscoveryLoading,
    onDiscoverRegions,
    onHealthRefresh,
    onBackupRestored,
    onRefreshAppSettings,
    blockedKeys,
    blockedNames,
    onToggleBlockedKey,
    onToggleBlockedName,
    contacts,
    channels,
    onBulkDeleteContacts,
    trackedTelemetryRepeaters,
    onToggleTrackedTelemetry,
    trackedTelemetryContacts,
    onToggleTrackedTelemetryContact,
    updates,
    onOpenUpdates,
    onApplyUpdate,
    onAutoUpdateChange,
    onCheckUpdates,
    onPatchUpdateSettings,
    updatesChecking,
    updatesApplying,
    onOpenManualHelp,
  } = props;
  const { t } = useTranslation();
  const capabilities = useCapabilities();
  const externalSidebarNav = props.externalSidebarNav === true;
  const desktopSection = props.externalSidebarNav ? props.desktopSection : undefined;

  // One section at a time, at every width. The narrow layout used to stack all of
  // them as an accordion because the burger was disabled in settings mode, leaving no
  // way to reach the section list — so the list had to be the page. The burger opens
  // it now, which makes settings navigate like every other view instead of being the
  // one place that scrolls through everything at once.
  const externalDesktopSidebarMode = externalSidebarNav;
  const [expandedSections, setExpandedSections] = useState<Record<SettingsSection, boolean>>({
    radio: false,
    proxy: false,
    local: false,
    access: false,
    notifications: false,
    updates: false,
    community: false,
    share: false,
    'radio-app': false,
    alerts: false,
    fanout: false,
    database: false,
    navigation: false,
    statistics: false,
    about: false,
  });

  // Refresh settings from server when modal opens
  useEffect(() => {
    if (open || pageMode) {
      onRefreshAppSettings();
    }
  }, [open, pageMode, onRefreshAppSettings]);

  const toggleSection = (section: SettingsSection) => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  const isSectionVisible = (section: SettingsSection) =>
    capabilities.canOpenSettingsSection(section) &&
    (externalDesktopSidebarMode ? desktopSection === section : expandedSections[section]);

  const showSectionButton = !externalDesktopSidebarMode;
  const shouldRenderSection = (section: SettingsSection) =>
    capabilities.canOpenSettingsSection(section) &&
    (!externalDesktopSidebarMode || desktopSection === section);

  const sectionWrapperClass = '';

  // 800px left a wide screen mostly empty while the form ran four screens tall.
  // Groups lay their fields out in columns from lg, so give them the room.
  const sectionContentClass = externalDesktopSidebarMode
    ? 'mx-auto w-full max-w-[800px] space-y-6 p-4 lg:max-w-[1080px] lg:p-6'
    : 'mx-auto w-full max-w-[800px] space-y-6 border-t border-input p-4 lg:max-w-[1080px] lg:p-6';

  const settingsContainerClass = externalDesktopSidebarMode
    ? 'w-full h-full min-w-0 overflow-x-hidden overflow-y-auto contain-[layout_paint]'
    : 'w-full h-full min-w-0 overflow-x-hidden overflow-y-auto space-y-3 contain-[layout_paint]';

  const sectionButtonClasses =
    'w-full flex items-center justify-between px-4 py-3 text-left hover:bg-muted/40 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset';

  const renderSectionHeader = (section: SettingsSection): ReactNode => {
    if (!showSectionButton) return null;
    const Icon = SETTINGS_SECTION_ICONS[section];
    return (
      <button
        type="button"
        data-settings-section={section}
        className={sectionButtonClasses}
        aria-expanded={expandedSections[section]}
        onClick={() => toggleSection(section)}
      >
        <span className="inline-flex items-center gap-2 font-medium" role="heading" aria-level={3}>
          <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span>{t(SETTINGS_SECTION_LABELS[section])}</span>
        </span>
        <span className="text-muted-foreground md:hidden" aria-hidden="true">
          {expandedSections[section] ? '−' : '+'}
        </span>
      </button>
    );
  };

  if (!pageMode && !open) {
    return null;
  }

  return (
    <div className={settingsContainerClass}>
      {shouldRenderSection('radio') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('radio')}
          {isSectionVisible('radio') && (
            <SettingsRadioSection
              config={config}
              health={health}
              appSettings={appSettings}
              pageMode={pageMode}
              onSave={onSave}
              onSaveAppSettings={onSaveAppSettings}
              onSetPrivateKey={onSetPrivateKey}
              onReboot={onReboot}
              onDisconnect={onDisconnect}
              onReconnect={onReconnect}
              onAdvertise={onAdvertise}
              meshDiscovery={meshDiscovery}
              meshDiscoveryLoadingTarget={meshDiscoveryLoadingTarget}
              onDiscoverMesh={onDiscoverMesh}
              regionDiscovery={regionDiscovery}
              regionDiscoveryLoading={regionDiscoveryLoading}
              onDiscoverRegions={onDiscoverRegions}
              onHealthRefresh={onHealthRefresh}
              onClose={onClose}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('proxy') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('proxy')}
          {isSectionVisible('proxy') && <SettingsProxySection className={sectionContentClass} />}
        </section>
      )}

      {shouldRenderSection('local') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('local')}
          {isSectionVisible('local') && (
            <SettingsLocalSection
              serverLabel={appSettings?.ui_preferences?.server_label}
              // Spread rather than rebuilt: listing the fields meant every theme
              // change quietly dropped whatever else lived in there.
              // The instance-wide choices (label, theme, allowed origins) are written with
              // the app settings, which only an administrator may change: the others keep
              // their per-device preferences and do not get the controls.
              onPersistServerLabel={
                capabilities.isAdmin
                  ? (server_label) =>
                      void onSaveAppSettings({
                        ui_preferences: {
                          ...appSettings?.ui_preferences,
                          nav_rail: appSettings?.ui_preferences?.nav_rail ?? [],
                          theme: appSettings?.ui_preferences?.theme ?? '',
                          server_label,
                        },
                      })
                  : undefined
              }
              onPersistTheme={
                capabilities.isAdmin
                  ? (theme) =>
                      void onSaveAppSettings({
                        ui_preferences: {
                          ...appSettings?.ui_preferences,
                          nav_rail: appSettings?.ui_preferences?.nav_rail ?? [],
                          theme,
                        },
                      })
                  : undefined
              }
              instanceReadOnly={!capabilities.isAdmin}
              allowedOrigins={appSettings?.auth_allowed_origins}
              onSaveAllowedOrigins={
                capabilities.isAdmin
                  ? (auth_allowed_origins) => onSaveAppSettings({ auth_allowed_origins })
                  : undefined
              }
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('access') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('access')}
          {isSectionVisible('access') && (
            <Suspense fallback={null}>
              <SettingsAccessSection className={sectionContentClass} />
            </Suspense>
          )}
        </section>
      )}

      {shouldRenderSection('notifications') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('notifications')}
          {isSectionVisible('notifications') && (
            <SettingsNotificationsSection
              appSettings={appSettings}
              onSaveAppSettings={onSaveAppSettings}
              contacts={contacts}
              channels={channels}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('updates') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('updates')}
          {isSectionVisible('updates') && (
            <SettingsUpdatesSection
              updates={updates ?? null}
              onCheckNow={onCheckUpdates}
              onApply={onApplyUpdate}
              onPatchSettings={
                onPatchUpdateSettings ??
                ((partial) => {
                  if (partial.auto_update != null) onAutoUpdateChange?.(partial.auto_update);
                })
              }
              onOpenManualHelp={onOpenManualHelp}
              checking={updatesChecking}
              applying={updatesApplying}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('community') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('community')}
          {isSectionVisible('community') && (
            <SettingsCommunitySection className={sectionContentClass} />
          )}
        </section>
      )}

      {shouldRenderSection('share') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('share')}
          {isSectionVisible('share') && (
            <SettingsShareSection
              className={sectionContentClass}
              onSaveAppSettings={onSaveAppSettings}
            />
          )}
        </section>
      )}

      {shouldRenderSection('radio-app') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('radio-app')}
          {isSectionVisible('radio-app') &&
            (appSettings ? (
              <SettingsRadioAppSection
                appSettings={appSettings}
                onSaveAppSettings={onSaveAppSettings}
                blockedKeys={blockedKeys}
                blockedNames={blockedNames}
                onToggleBlockedKey={onToggleBlockedKey}
                onToggleBlockedName={onToggleBlockedName}
                contacts={contacts}
                onBulkDeleteContacts={onBulkDeleteContacts}
                trackedTelemetryRepeaters={trackedTelemetryRepeaters}
                onToggleTrackedTelemetry={onToggleTrackedTelemetry}
                trackedTelemetryContacts={trackedTelemetryContacts}
                onToggleTrackedTelemetryContact={onToggleTrackedTelemetryContact}
                className={sectionContentClass}
              />
            ) : (
              <div className={sectionContentClass}>
                <div className="rounded-md border border-input bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                  {t('settings.radioApp.loading')}
                </div>
              </div>
            ))}
        </section>
      )}

      {shouldRenderSection('alerts') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('alerts')}
          {isSectionVisible('alerts') &&
            (appSettings ? (
              <SettingsAlertsSection
                appSettings={appSettings}
                onSaveAppSettings={onSaveAppSettings}
                contacts={contacts}
                trackedTelemetryRepeaters={trackedTelemetryRepeaters}
                trackedTelemetryContacts={trackedTelemetryContacts}
                className={sectionContentClass}
              />
            ) : (
              <div className={sectionContentClass}>
                <div className="rounded-md border border-input bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                  {t('settings.alerts.loading')}
                </div>
              </div>
            ))}
        </section>
      )}

      {shouldRenderSection('database') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('database')}
          {isSectionVisible('database') &&
            (appSettings ? (
              <SettingsDatabaseSection
                appSettings={appSettings}
                health={health}
                onSaveAppSettings={onSaveAppSettings}
                onHealthRefresh={onHealthRefresh}
                onBackupRestored={onBackupRestored}
                className={sectionContentClass}
              />
            ) : (
              <div className={sectionContentClass}>
                <div className="rounded-md border border-input bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                  {t('settings.database.loading')}
                </div>
              </div>
            ))}
        </section>
      )}

      {shouldRenderSection('fanout') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('fanout')}
          {isSectionVisible('fanout') && (
            <SettingsFanoutSection
              health={health}
              onHealthRefresh={onHealthRefresh}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('navigation') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('navigation')}
          {isSectionVisible('navigation') && (
            <div className={sectionContentClass}>
              <SettingsNavigationSection
                order={appSettings?.ui_preferences?.nav_rail ?? []}
                // Spread: the preferences live in one object, and rebuilding it from a
                // few fields dropped the others (the start page, the server label).
                onChange={(nav_rail) =>
                  void onSaveAppSettings({
                    ui_preferences: {
                      ...appSettings?.ui_preferences,
                      nav_rail,
                      theme: appSettings?.ui_preferences?.theme ?? '',
                    },
                  })
                }
                startView={appSettings?.ui_preferences?.start_view ?? ''}
                startViewReadOnly={!capabilities.isAdmin}
                onStartViewChange={(start_view) =>
                  void onSaveAppSettings({
                    ui_preferences: {
                      ...appSettings?.ui_preferences,
                      nav_rail: appSettings?.ui_preferences?.nav_rail ?? [],
                      theme: appSettings?.ui_preferences?.theme ?? '',
                      start_view,
                    },
                  })
                }
              />
            </div>
          )}
        </section>
      )}

      {shouldRenderSection('statistics') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('statistics')}
          {isSectionVisible('statistics') && (
            <SettingsStatisticsSection className={sectionContentClass} />
          )}
        </section>
      )}

      {shouldRenderSection('about') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('about')}
          {isSectionVisible('about') && (
            <SettingsAboutSection
              health={health}
              className={sectionContentClass}
              updates={updates}
              onOpenUpdates={
                onOpenUpdates ??
                (() => {
                  window.location.hash = getSettingsHash('updates');
                })
              }
            />
          )}
        </section>
      )}
    </div>
  );
}
