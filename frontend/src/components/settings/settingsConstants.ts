import {
  BarChart3,
  PanelLeft,
  Bell,
  Database,
  Globe,
  Info,
  Link2,
  MonitorCog,
  RadioTower,
  RefreshCw,
  Share2,
  SlidersHorizontal,
  TriangleAlert,
  Waypoints,
  type LucideIcon,
} from 'lucide-react';

export type SettingsSection =
  | 'radio'
  | 'proxy'
  | 'local'
  | 'notifications'
  | 'updates'
  | 'community'
  | 'share'
  | 'radio-app'
  | 'alerts'
  | 'database'
  | 'fanout'
  | 'statistics'
  | 'navigation'
  | 'about';

export const SETTINGS_SECTION_ORDER: SettingsSection[] = [
  'radio',
  'proxy',
  'local',
  'notifications',
  'updates',
  'community',
  'share',
  'fanout',
  'radio-app',
  'alerts',
  'database',
  'navigation',
  'statistics',
  'about',
];

/** i18n key ids. Translate at render with t(SETTINGS_SECTION_LABELS[section]). */
export const SETTINGS_SECTION_LABELS: Record<SettingsSection, string> = {
  radio: 'settingsNav.radio',
  proxy: 'settingsNav.proxy',
  local: 'settingsNav.local',
  notifications: 'settingsNav.notifications',
  updates: 'settingsNav.updates',
  community: 'settingsNav.community',
  share: 'settingsNav.share',
  'radio-app': 'settingsNav.radioApp',
  alerts: 'settingsNav.alerts',
  database: 'settingsNav.database',
  fanout: 'settingsNav.fanout',
  navigation: 'settingsNav.navigation',
  statistics: 'settingsNav.statistics',
  about: 'settingsNav.about',
};

export const SETTINGS_SECTION_ICONS: Record<SettingsSection, LucideIcon> = {
  radio: RadioTower,
  proxy: Waypoints,
  local: MonitorCog,
  notifications: Bell,
  updates: RefreshCw,
  community: Globe,
  share: Link2,
  'radio-app': SlidersHorizontal,
  alerts: TriangleAlert,
  database: Database,
  fanout: Share2,
  navigation: PanelLeft,
  statistics: BarChart3,
  about: Info,
};
