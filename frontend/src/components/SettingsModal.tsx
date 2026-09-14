import { useState, useEffect, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  CommunityStatus,
  Contact,
  HealthStatus,
  RadioAdvertMode,
  RadioConfig,
  RadioConfigUpdate,
  RadioDiscoveryResponse,
  RadioDiscoveryTarget,
  RadioRegionDiscoveryResponse,
} from '../types';
import type { LocalLabel } from '../utils/localLabel';
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
import { SettingsRadioAppSection } from './settings/SettingsRadioAppSection';
import { SettingsFanoutSection } from './settings/SettingsFanoutSection';
import { SettingsDatabaseSection } from './settings/SettingsDatabaseSection';
import { SettingsStatisticsSection } from './settings/SettingsStatisticsSection';
import { SettingsAboutSection } from './settings/SettingsAboutSection';

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
  onReconnect: () => Promise<void>;
  onAdvertise: (mode: RadioAdvertMode) => Promise<void>;
  meshDiscovery: RadioDiscoveryResponse | null;
  meshDiscoveryLoadingTarget: RadioDiscoveryTarget | null;
  onDiscoverMesh: (target: RadioDiscoveryTarget) => Promise<void>;
  regionDiscovery: RadioRegionDiscoveryResponse | null;
  regionDiscoveryLoading: boolean;
  onDiscoverRegions: (publicKeys?: string[]) => Promise<void>;
  onHealthRefresh: () => Promise<void>;
  onRefreshAppSettings: () => Promise<void>;
  onLocalLabelChange?: (label: LocalLabel) => void;
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
  onCommunityStatusChange?: (status: CommunityStatus) => void;
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
    onRefreshAppSettings,
    onLocalLabelChange,
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
    onCommunityStatusChange,
  } = props;
  const { t } = useTranslation();
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
    notifications: false,
    community: false,
    'radio-app': false,
    fanout: false,
    database: false,
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
    externalDesktopSidebarMode ? desktopSection === section : expandedSections[section];

  const showSectionButton = !externalDesktopSidebarMode;
  const shouldRenderSection = (section: SettingsSection) =>
    !externalDesktopSidebarMode || desktopSection === section;

  const sectionWrapperClass = '';

  // 800px left a wide screen mostly empty while the form ran four screens tall.
  // Groups lay their fields out in columns from lg, so give them the room.
  const sectionContentClass = externalDesktopSidebarMode
    ? 'mx-auto w-full max-w-[800px] space-y-6 p-4 lg:max-w-[1080px] lg:p-6'
    : 'mx-auto w-full max-w-[800px] space-y-6 border-t border-input p-4 lg:max-w-[1080px] lg:p-6';

  const settingsContainerClass = externalDesktopSidebarMode
    ? 'w-full h-full min-w-0 overflow-x-hidden overflow-y-auto [contain:layout_paint]'
    : 'w-full h-full min-w-0 overflow-x-hidden overflow-y-auto space-y-3 [contain:layout_paint]';

  const sectionButtonClasses =
    'w-full flex items-center justify-between px-4 py-3 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset';

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
              onLocalLabelChange={onLocalLabelChange}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('notifications') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('notifications')}
          {isSectionVisible('notifications') && (
            <SettingsNotificationsSection
              contacts={contacts}
              channels={channels}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('community') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('community')}
          {isSectionVisible('community') && (
            <SettingsCommunitySection
              className={sectionContentClass}
              onStatusChange={onCommunityStatusChange}
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
            <SettingsAboutSection health={health} className={sectionContentClass} />
          )}
        </section>
      )}
    </div>
  );
}
